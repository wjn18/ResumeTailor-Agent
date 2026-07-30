import unittest

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.services.jd_web_extractor import (
    JDURLSecurityError,
    extract_jd_text_from_url,
)


PUBLIC_URL = "https://93.184.216.34/jobs/backend-engineer"


class JDWebExtractorTests(unittest.TestCase):
    def test_extracts_job_posting_json_ld_first(self):
        html = """
        <html>
          <head>
            <title>Backend Engineer | Example</title>
            <script type="application/ld+json">
              {
                "@context": "https://schema.org",
                "@type": "JobPosting",
                "title": "Backend Engineer",
                "hiringOrganization": {"name": "Example Ltd"},
                "description": "<p>Build reliable API services.</p>",
                "responsibilities": [
                  "Design and maintain FastAPI services.",
                  "Review production changes."
                ],
                "qualifications": [
                  "Experience with Python and SQL.",
                  "Clear technical communication."
                ]
              }
            </script>
          </head>
          <body><nav>Navigation</nav><main>Unrelated short content.</main></body>
        </html>
        """
        client = _mock_client(
            lambda _request: httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                content=html.encode(),
            )
        )

        result = extract_jd_text_from_url(PUBLIC_URL, client=client)

        self.assertEqual(result.extraction_method, "json_ld")
        self.assertIn("Backend Engineer", result.raw_text)
        self.assertIn("Example Ltd", result.raw_text)
        self.assertIn("FastAPI", result.raw_text)
        self.assertNotIn("Navigation", result.raw_text)

    def test_falls_back_to_visible_job_container(self):
        html = """
        <html>
          <head><title>软件开发工程师 - 示例科技</title></head>
          <body>
            <header>首页 产品 新闻 联系我们</header>
            <main>
              <section class="job-description">
                <h1>软件开发工程师</h1>
                <h2>岗位职责</h2>
                <p>负责后端服务设计、接口开发、单元测试和线上问题排查。</p>
                <h2>任职要求</h2>
                <p>熟悉 Python、FastAPI、MySQL，具备清晰的技术沟通能力。</p>
              </section>
            </main>
            <footer>隐私政策 Cookie 设置</footer>
          </body>
        </html>
        """
        client = _mock_client(
            lambda _request: httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=html.encode("utf-8"),
            )
        )

        result = extract_jd_text_from_url(PUBLIC_URL, client=client)

        self.assertEqual(result.extraction_method, "html")
        self.assertIn("岗位职责", result.raw_text)
        self.assertIn("FastAPI", result.raw_text)
        self.assertNotIn("隐私政策", result.raw_text)

    def test_rejects_private_ip_url(self):
        with self.assertRaisesRegex(
            JDURLSecurityError,
            "非公网 IP",
        ):
            extract_jd_text_from_url("http://127.0.0.1/jobs")

    def test_revalidates_redirect_target(self):
        def handler(request: httpx.Request):
            if request.url.host == "93.184.216.34":
                return httpx.Response(
                    302,
                    headers={"location": "http://127.0.0.1/internal"},
                )
            raise AssertionError("Private redirect target must not be fetched.")

        with self.assertRaisesRegex(
            JDURLSecurityError,
            "非公网 IP",
        ):
            extract_jd_text_from_url(
                PUBLIC_URL,
                client=_mock_client(handler),
            )

    def test_http_endpoint_exposes_security_error(self):
        response = TestClient(app).post(
            "/jds/extract-url",
            json={"url": "http://127.0.0.1/jobs"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("非公网 IP", response.json()["detail"])


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


if __name__ == "__main__":
    unittest.main()
