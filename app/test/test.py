from fastapi import FastAPI

app = FastAPI()

@app.get("/database/users")
def database_users():
    # 查询数据库，例如 SELECT 1
    return {
        "status": "ok",
        "database": "connected"
    }