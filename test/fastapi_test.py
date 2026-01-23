from fastapi import FastAPI
from pydantic import BaseModel

# 初始化 FastAPI 应用实例
app = FastAPI()

# 定义数据模型（用于请求体验证）
class Item(BaseModel):
    name: str
    price: float
    is_offer: bool | None = None  # 可选参数

# 定义 GET 接口：路径参数 + 简单返回
@app.get("/items/{item_id}")
async def read_item(item_id: int, q: str | None = None):  # q 是可选查询参数
    return {"item_id": item_id, "q": q}

# 定义 PUT 接口：接收请求体并返回
@app.put("/items/{item_id}")
async def update_item(item_id: int, item: Item):
    return {"item_name": item.name, "item_id": item_id, "price": item.price}

# 运行方式：在终端执行 uvicorn 文件名:app --reload
# 例如：uvicorn main:app --reload