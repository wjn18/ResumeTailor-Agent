from datetime import datetime, timezone
import ipaddress
import json
import re
import socket
from typing import Callable
from urllib.parse import urljoin

from bs4 import BeautifulSoup, UnicodeDammit
import httpx
from playwright.sync_api import (
    Error as PlaywrightError,
    Request as PlaywrightRequest,
    Route as PlaywrightRoute,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from app.schemas.jds import ExtractedJDText


MAX_REDIRECTS = 4
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_BROWSER_HTML_BYTES = 5 * 1024 * 1024
MIN_EXTRACTED_TEXT_LENGTH = 80
BROWSER_NAVIGATION_TIMEOUT_MS = 25_000
BROWSER_NETWORK_IDLE_TIMEOUT_MS = 6_000
BROWSER_RENDER_SETTLE_MS = 1_500
BROWSER_CONTENT_RETRIES = 4
ALLOWED_CONTENT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
)
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
BLOCKED_HOST_SUFFIXES = (
    ".internal",
    ".intranet",
    ".local",
    ".localhost",
)
USER_AGENT = (
    "Mozilla/5.0 (compatible; ResumeTailor-JDExtractor/0.1; "
    "+https://github.com/wjn18/ResumeTailor-Agent)"
)
VERIFICATION_SELECTORS = (
    "iframe[src*='captcha']",
    "[class*='captcha']",
    "[id*='captcha']",
    "[class*='verify']",
    "[id*='verify']",
)
VERIFICATION_TEXT_MARKERS = (
    "安全验证",
    "请完成验证",
    "点击验证",
    "滑动验证",
    "访问过于频繁",
    "异常访问",
    "captcha",
    "verify you are human",
)

BrowserRenderer = Callable[[str], ExtractedJDText]


class JDURLSecurityError(ValueError):
    pass


class JDPageFetchError(RuntimeError):
    pass


def extract_jd_text_from_url(
    source_url: str,
    client: httpx.Client | None = None,
    browser_renderer: BrowserRenderer | None = None,
    use_browser_fallback: bool = True,
) -> ExtractedJDText:
    try:
        return _extract_jd_text_with_http(source_url, client)
    except JDPageFetchError as static_error:
        if not use_browser_fallback:
            raise

        renderer = browser_renderer or _extract_jd_text_with_browser
        try:
            return renderer(source_url)
        except JDURLSecurityError:
            raise
        except JDPageFetchError as browser_error:
            raise JDPageFetchError(
                "静态抓取失败："
                f"{static_error} 浏览器渲染抓取失败：{browser_error}"
            ) from browser_error


def _extract_jd_text_with_http(
    source_url: str,
    client: httpx.Client | None = None,
) -> ExtractedJDText:
    current_url = _validate_public_url(source_url)
    owns_client = client is None
    if client is None:
        client = httpx.Client(
            timeout=httpx.Timeout(
                connect=8,
                read=15,
                write=8,
                pool=8,
            ),
            follow_redirects=False,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,text/plain",
            },
        )

    try:
        for redirect_count in range(MAX_REDIRECTS + 1):
            response, body = _fetch_response(client, current_url)
            if response.status_code in REDIRECT_STATUSES:
                if redirect_count == MAX_REDIRECTS:
                    raise JDPageFetchError(
                        f"网页重定向超过 {MAX_REDIRECTS} 次。"
                    )
                location = response.headers.get("location")
                if not location:
                    raise JDPageFetchError("网页返回了没有目标地址的重定向。")
                current_url = _validate_public_url(
                    urljoin(str(current_url), location)
                )
                continue

            page_title, raw_text, method = _extract_page_text(
                body,
                response.headers.get("content-type", ""),
            )
            if len(raw_text) < MIN_EXTRACTED_TEXT_LENGTH:
                raise JDPageFetchError(
                    "网页正文过短，未识别到可用的岗位描述文本。"
                )
            return ExtractedJDText(
                source_url=source_url,
                final_url=str(current_url),
                page_title=page_title,
                raw_text=raw_text,
                text_length=len(raw_text),
                extraction_method=method,
                fetched_at=datetime.now(timezone.utc),
            )
    finally:
        if owns_client:
            client.close()

    raise JDPageFetchError("网页抓取未返回结果。")


def _extract_jd_text_with_browser(source_url: str) -> ExtractedJDText:
    validated_url = _validate_public_url(source_url)
    validated_hosts: set[tuple[str, int]] = set()

    try:
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright)
            try:
                context = browser.new_context(
                    locale="zh-CN",
                    viewport={"width": 1440, "height": 1000},
                )
                page = context.new_page()
                navigation_urls: list[str] = []
                page.on(
                    "framenavigated",
                    lambda frame: (
                        navigation_urls.append(frame.url)
                        if frame == page.main_frame
                        else None
                    ),
                )
                page.route(
                    "**/*",
                    lambda route, request: _handle_browser_request(
                        route,
                        request,
                        validated_hosts,
                    ),
                )
                response = page.goto(
                    str(validated_url),
                    wait_until="domcontentloaded",
                    timeout=BROWSER_NAVIGATION_TIMEOUT_MS,
                )
                if response is not None and response.status >= 400:
                    raise JDPageFetchError(
                        f"浏览器访问目标网页时返回 HTTP {response.status}。"
                    )

                try:
                    page.wait_for_load_state(
                        "networkidle",
                        timeout=BROWSER_NETWORK_IDLE_TIMEOUT_MS,
                    )
                except PlaywrightTimeoutError:
                    pass
                page.wait_for_timeout(BROWSER_RENDER_SETTLE_MS)

                if any(
                    _is_verification_url(url)
                    for url in (*navigation_urls, page.url)
                ):
                    raise JDPageFetchError(
                        "目标网站跳转到了安全验证页面，需要人工验证。"
                    )
                if not page.url.startswith(("http://", "https://")):
                    raise JDPageFetchError(
                        "浏览器未停留在可读取的 HTTP/HTTPS 页面，"
                        f"最终地址为 {page.url!r}。"
                    )

                html = _read_stable_page_content(page)
                final_url = str(_validate_public_url(page.url))
                if len(html.encode("utf-8")) > MAX_BROWSER_HTML_BYTES:
                    raise JDPageFetchError(
                        "浏览器渲染后的网页超过 5 MB，已停止提取。"
                    )

                page_title, raw_text, method = _extract_page_text(
                    html.encode("utf-8"),
                    "text/html; charset=utf-8",
                )
                verification_message = _detect_verification_page(
                    page,
                    page_title,
                    raw_text,
                )
                if verification_message:
                    raise JDPageFetchError(verification_message)
                if len(raw_text) < MIN_EXTRACTED_TEXT_LENGTH:
                    raise JDPageFetchError(
                        "浏览器已完成页面渲染，但仍未识别到可用的岗位描述文本。"
                    )

                return ExtractedJDText(
                    source_url=source_url,
                    final_url=final_url,
                    page_title=page_title,
                    raw_text=raw_text,
                    text_length=len(raw_text),
                    extraction_method=f"playwright_{method}",
                    fetched_at=datetime.now(timezone.utc),
                )
            finally:
                browser.close()
    except JDPageFetchError:
        raise
    except PlaywrightTimeoutError as exc:
        raise JDPageFetchError("浏览器等待网页渲染超时。") from exc
    except PlaywrightError as exc:
        message = str(exc)
        if "Executable doesn't exist" in message:
            raise JDPageFetchError(
                "未安装 Chromium，请运行 "
                "`uv run playwright install chromium`。"
            ) from exc
        raise JDPageFetchError(f"浏览器抓取失败：{message}") from exc


def _read_stable_page_content(page) -> str:
    for attempt in range(BROWSER_CONTENT_RETRIES):
        try:
            return page.content()
        except PlaywrightError as exc:
            if "page is navigating" not in str(exc).casefold():
                raise
            if attempt == BROWSER_CONTENT_RETRIES - 1:
                break
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5_000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(500)
    raise JDPageFetchError(
        "目标网页持续跳转或刷新，无法取得稳定的页面内容。"
    )


def _launch_browser(playwright):
    try:
        return playwright.chromium.launch(headless=True)
    except PlaywrightError as bundled_error:
        if "Executable doesn't exist" not in str(bundled_error):
            raise
        try:
            return playwright.chromium.launch(channel="chrome", headless=True)
        except PlaywrightError as system_error:
            raise JDPageFetchError(
                "未找到可用的 Chromium 或 Google Chrome，请运行 "
                "`uv run playwright install chromium`。"
            ) from system_error


def _handle_browser_request(
    route: PlaywrightRoute,
    request: PlaywrightRequest,
    validated_hosts: set[tuple[str, int]],
) -> None:
    request_url = request.url
    try:
        parsed_url = httpx.URL(request_url)
    except (TypeError, ValueError):
        route.abort("blockedbyclient")
        return

    if parsed_url.scheme in {"about", "blob", "data"}:
        route.continue_()
        return
    if parsed_url.scheme not in {"http", "https"}:
        route.abort("blockedbyclient")
        return

    port = parsed_url.port or (443 if parsed_url.scheme == "https" else 80)
    host_key = (parsed_url.host.rstrip(".").casefold(), port)
    try:
        if host_key not in validated_hosts:
            _validate_public_url(request_url)
            validated_hosts.add(host_key)
    except JDURLSecurityError:
        route.abort("blockedbyclient")
        return
    route.continue_()


def _is_verification_url(url: str) -> bool:
    normalized = url.casefold()
    return any(
        marker in normalized
        for marker in (
            "/security.",
            "/captcha",
            "/verify",
            "challenge",
        )
    )


def _detect_verification_page(
    page,
    page_title: str | None,
    raw_text: str,
) -> str | None:
    combined_text = f"{page_title or ''}\n{raw_text}".casefold()
    if any(marker.casefold() in combined_text for marker in VERIFICATION_TEXT_MARKERS):
        return "目标网站返回了安全验证或访问频率限制页面，需要人工验证。"

    for selector in VERIFICATION_SELECTORS:
        try:
            if page.locator(selector).count() > 0:
                return "目标网站返回了验证码页面，需要人工验证。"
        except PlaywrightError:
            continue
    return None


def _fetch_response(
    client: httpx.Client,
    url: httpx.URL,
) -> tuple[httpx.Response, bytes]:
    try:
        with client.stream("GET", url) as response:
            if response.status_code in REDIRECT_STATUSES:
                return response, b""
            _validate_response(response)
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise JDPageFetchError(
                        "网页响应超过 2 MB，已停止下载。"
                    )
            return response, bytes(body)
    except JDPageFetchError:
        raise
    except httpx.TimeoutException as exc:
        raise JDPageFetchError("抓取网页超时。") from exc
    except httpx.RequestError as exc:
        raise JDPageFetchError(f"无法访问网页：{exc}") from exc


def _validate_response(response: httpx.Response) -> None:
    if response.status_code >= 400:
        raise JDPageFetchError(
            f"目标网页返回 HTTP {response.status_code}。"
        )

    content_type = response.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().casefold()
    if media_type and not any(
        media_type == allowed_type
        for allowed_type in ALLOWED_CONTENT_TYPES
    ):
        raise JDPageFetchError(
            f"目标地址不是 HTML 或纯文本网页：{media_type}。"
        )


def _validate_public_url(raw_url: str) -> httpx.URL:
    try:
        url = httpx.URL(raw_url.strip())
    except (TypeError, ValueError) as exc:
        raise JDURLSecurityError("URL 格式无效。") from exc

    if url.scheme not in {"http", "https"}:
        raise JDURLSecurityError("只允许 http:// 或 https:// URL。")
    if not url.host:
        raise JDURLSecurityError("URL 缺少有效域名。")
    if url.username or url.password:
        raise JDURLSecurityError("URL 不允许包含用户名或密码。")
    effective_port = url.port or (443 if url.scheme == "https" else 80)
    if effective_port not in {80, 443}:
        raise JDURLSecurityError("只允许访问 80 或 443 端口。")

    hostname = url.host.rstrip(".").casefold()
    if hostname == "localhost" or hostname.endswith(BLOCKED_HOST_SUFFIXES):
        raise JDURLSecurityError("不允许访问本机或内部网络地址。")

    addresses = _resolve_host_addresses(hostname, effective_port)
    if not addresses or any(not address.is_global for address in addresses):
        raise JDURLSecurityError("URL 解析到了非公网 IP 地址，已拒绝访问。")
    return url


def _resolve_host_addresses(
    hostname: str,
    port: int,
) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return {ipaddress.ip_address(hostname)}
    except ValueError:
        pass

    try:
        address_info = socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise JDURLSecurityError("无法解析 URL 域名。") from exc

    return {
        ipaddress.ip_address(item[4][0])
        for item in address_info
    }


def _extract_page_text(
    body: bytes,
    content_type: str,
) -> tuple[str | None, str, str]:
    decoded = _decode_body(body, content_type)
    soup = BeautifulSoup(decoded, "html.parser")
    page_title = _clean_inline_text(
        soup.title.get_text(" ", strip=True)
        if soup.title
        else ""
    ) or None

    structured_text = _extract_job_posting_json_ld(soup)
    if len(structured_text) >= MIN_EXTRACTED_TEXT_LENGTH:
        return page_title, structured_text, "json_ld"

    return page_title, _extract_visible_job_text(soup, page_title), "html"


def _decode_body(body: bytes, content_type: str) -> str:
    charset_match = re.search(
        r"charset\s*=\s*[\"']?([^;\"'\s]+)",
        content_type,
        flags=re.IGNORECASE,
    )
    if charset_match:
        try:
            return body.decode(charset_match.group(1), errors="replace")
        except LookupError:
            pass
    return UnicodeDammit(body).unicode_markup or body.decode(
        "utf-8",
        errors="replace",
    )


def _extract_job_posting_json_ld(soup: BeautifulSoup) -> str:
    for script in soup.find_all(
        "script",
        attrs={
            "type": lambda value: (
                isinstance(value, str)
                and value.split(";", 1)[0].strip().casefold()
                == "application/ld+json"
            )
        },
    ):
        script_text = script.string or script.get_text()
        if not script_text.strip():
            continue
        try:
            payload = json.loads(script_text)
        except json.JSONDecodeError:
            continue
        for item in _iter_json_ld_objects(payload):
            if _is_job_posting(item):
                return _format_job_posting(item)
    return ""


def _iter_json_ld_objects(payload):
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_json_ld_objects(item)
        return
    if not isinstance(payload, dict):
        return
    yield payload
    graph = payload.get("@graph")
    if isinstance(graph, (dict, list)):
        yield from _iter_json_ld_objects(graph)


def _is_job_posting(item: dict) -> bool:
    item_type = item.get("@type")
    types = item_type if isinstance(item_type, list) else [item_type]
    return any(
        isinstance(value, str)
        and (
            value.strip().casefold() == "jobposting"
            or value.rstrip("/").casefold().endswith("/jobposting")
        )
        for value in types
    )


def _format_job_posting(item: dict) -> str:
    sections = []

    def add(label: str, value) -> None:
        text = _json_ld_value_to_text(value)
        if text:
            sections.append(f"{label}\n{text}")

    add("岗位名称", item.get("title"))
    organization = item.get("hiringOrganization")
    if isinstance(organization, dict):
        add("公司名称", organization.get("name"))
    else:
        add("公司名称", organization)
    add("岗位描述", item.get("description"))
    add("岗位职责", item.get("responsibilities"))
    add("任职要求", item.get("qualifications"))
    add("技能要求", item.get("skills"))
    add("经验要求", item.get("experienceRequirements"))
    add("教育要求", item.get("educationRequirements"))
    add("工作类型", item.get("employmentType"))
    add("岗位福利", item.get("jobBenefits"))
    return _normalize_text("\n\n".join(sections))


def _json_ld_value_to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _html_fragment_to_text(value)
    if isinstance(value, list):
        return "\n".join(
            text
            for item in value
            if (text := _json_ld_value_to_text(item))
        )
    if isinstance(value, dict):
        preferred_keys = (
            "name",
            "value",
            "description",
            "addressLocality",
            "addressRegion",
            "addressCountry",
        )
        return "\n".join(
            text
            for key in preferred_keys
            if key in value
            if (text := _json_ld_value_to_text(value[key]))
        )
    return str(value)


def _html_fragment_to_text(fragment: str) -> str:
    return BeautifulSoup(fragment, "html.parser").get_text("\n", strip=True)


def _extract_visible_job_text(
    soup: BeautifulSoup,
    page_title: str | None,
) -> str:
    for tag_name in (
        "script",
        "style",
        "nav",
        "footer",
        "header",
        "aside",
        "form",
        "noscript",
        "svg",
    ):
        for tag in soup.find_all(tag_name):
            tag.decompose()

    selectors = (
        "[itemprop='description']",
        "#job-description",
        "[id*='job-description']",
        "[class*='job-description']",
        "[class*='job-content']",
        "[class*='job-sec']",
        "[id*='job_detail']",
        "[class*='job-detail']",
        "main",
        "article",
    )
    candidates = [
        element
        for selector in selectors
        for element in soup.select(selector)
    ]
    container = max(
        candidates,
        key=lambda element: len(element.get_text(" ", strip=True)),
        default=soup.body or soup,
    )
    raw_text = container.get_text("\n", strip=True)
    if page_title and page_title.casefold() not in raw_text.casefold():
        raw_text = f"{page_title}\n{raw_text}"
    return _normalize_text(raw_text)


def _normalize_text(text: str) -> str:
    lines = []
    seen = set()
    for raw_line in text.splitlines():
        line = _clean_inline_text(raw_line)
        key = line.casefold()
        if not line or key in seen:
            continue
        seen.add(key)
        lines.append(line)
    return "\n".join(lines)


def _clean_inline_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
