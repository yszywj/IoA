import json
import uuid
from copy import deepcopy
from datetime import datetime
from urllib.parse import quote, unquote

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pymilvus import connections
from starlette.middleware.cors import CORSMiddleware

from common.log import logger
from common.types import (
    AgentEntry,
    AgentInfo,
    AgentMessage,
    AgentRegistryQueryParam,
    AgentRegistryRetrivalParam,
    AgentRegistryTeamupOutput,
    AgentRegistryTeamupParam,
    ChatRecordFetchParam,
)
from common.utils.database_utils import AutoStoredDict
from common.utils.milvus_utils import ConfigMilvusWrapper

app = FastAPI()

origins = ["*"]
# 来源域名，*为通配符，允许所有

app.add_middleware(
    # 注册中间件，参数（固定核心参数（中间件类型）+可变参数（选择的中间件专属参数，核心参数的构造函数所需要的参数））
    CORSMiddleware,
    allow_origins=origins,
    # 允许所有访问域名
    allow_credentials=True,
    # 允许携带cookie
    allow_methods=["*"],
    # 允许所有http方法
    allow_headers=["*"],
    # 允许携带所有头信息（允许的表头对，如客户端（浏览器）类型）
)


class ConnectionManager:
    def __init__(self):
        self.agent_to_websocket: dict[str, WebSocket] = {}
        # 存储agent名称到websocket连接的映射，声明变量并说明其类型为字典，键为字符串类型，值为WebSocket类型
        # WebSocket是FastAPI中用于处理WebSocket连接的类，有所有关于WebSocket的方法

    # 建立连接
    async def connect(self, websocket: WebSocket, agent_name: str) -> str:
        await websocket.accept()
        #等待websocket连接被接受
        self.agent_to_websocket[agent_name] = websocket
        print(self.agent_to_websocket)

    # 断开连接
    async def disconnect(self, agent_name: str):
        self.agent_to_websocket.pop(agent_name, None)

    # 查找键名并发送个人消息
    async def send_personal_message(self, receiver: str, message: AgentMessage):
        try:
            websocket = self.agent_to_websocket[receiver]
        except:
            logger.error(f"Failed to find the websocket for {receiver}")
        await websocket.send_text(message.model_dump_json())


class AgentRegistry:
    """
    Agent Registry block. Providing agent registering and querying services based on Milvus vector database.
    """

    def __init__(self):
        self.agents = ConfigMilvusWrapper("configs/agent_registry.yaml")
        # 创建一个名为 AgentRegistry 的结构化集合，保存内容包括Agent 的唯一标识（主键，不可重复），
        # Agent 的名称，Agent 的描述信息，Agent 的类型，Agent 的创建时间，自动生成的向量（用于相似度检索）

    # 注册agent
    async def register(self, agent: AgentInfo) -> None:
    # AgentInfo,传输数据格式，继承自BaseModel，自带数据校验。
        if agent.name in self.agents:
            return agent.name
        timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        self.agents[agent.name] = AgentEntry(
            name=agent.name,
            desc=agent.desc,
            type=agent.type,
            created_at=timestamp,
        )

    # 检索agent，AgentRegistryRetrivalParam包括发起者标识和agent能力关键词列表。
    async def retrieve(self, param: AgentRegistryRetrivalParam) -> list[AgentInfo]:
        res = self.agents.search_via_config(param.capabilities)
        deduplicate_ids = set()
        deduplicate_hits = []
        for hits in res:
            for hit in hits:
        # res为多维列表，最上层为发起者语句中的不同能力，hits为一种能力下对应的检索匹配结果，hit是其中一个匹配的agent信息字典。
                if hit.get("name") not in deduplicate_ids:
                # 查重，防止同一个agent有多个匹配结果
                    deduplicate_hits.append(hit)
                    deduplicate_ids.add(hit.get("name"))
        return [AgentInfo(name=hit.get("name"), type=hit.get("type"), desc=hit.get("desc")) for hit in deduplicate_hits]

    # 精准查询agent，支持单个名称查询和多个名称批量查询。入参为名称字符串或名称字符串列表。返回值为单个AgentInfo对象或AgentInfo对象列表。
    async def query(self, name: list[str] | str) -> list[AgentInfo | None] | AgentInfo | None:
        result = []
        candidates = name if isinstance(name, list) else [name]
        # 统一格式为列表
        for agent_name in candidates:
            if agent_name in self.agents:
                result.append(
                    AgentInfo(
                        name=agent_name,
                        desc=self.agents[agent_name].get("desc"),
                        type=self.agents[agent_name].get("type"),
                    )
                )
            else:
                result.append(None)
        if isinstance(name, str):
            return result[0]
        else:
            return result


class SessionManager:
    """
    Session Manager block. Maintaining the basic group chat information.
    """

    def __init__(self):
        self.sessions = AutoStoredDict("database/server/sessions.db", tablename="sessions")
        # 创建一个自动存储的字典对象，用于存储会话信息，数据保存在指定的本地数据库文件和表中。

    async def teamup(self, agent_names: list[str]) -> AgentRegistryTeamupOutput:
        comm_id = uuid.uuid4().hex
        # 生成此次会话的唯一uid，并转为32位纯字符串
        session_group = []
        for name in agent_names:
            if name in agent_registry.agents.keys():
                session_group.append(name)
        # 筛选出已注册的agent名称，防止出现不存在（未注册）的agent
        self.sessions[comm_id] = session_group
        # 存储会话信息，键为comm_id，值为参与会话的agent名称列表
        return {"comm_id": comm_id, "agent_names": session_group}

# 装饰后的函数会在收到访问“/ws/{agent_name}”的WebSocket请求时被调用。且会将URL中的agent_name部分作为参数传递给函数。
@app.websocket("/ws/{agent_name}")
async def websocket_endpoint(websocket: WebSocket, agent_name: str):
    """
    Endpoint for receiving agent messages
    """
    await connection_manager.connect(websocket, agent_name)
    # 建立WebSocket连接，并将连接与agent名称关联。connection_manager是websocket连接管理器。
    agent_name = unquote(agent_name)
    # 解码url中的agent名称，把url转为字符串（agent名称str）。

    try:
        while True:
        # websocket长连接
            data = await websocket.receive_text()
            # 异步阻塞接收信息
            try:
                parsed_data = AgentMessage.model_validate_json(data)
                # 将接收到的JSON字符串解析为AgentMessage对象，同时校验必填字段是否存在以及类型长度是否匹配
            except:
                logger.error("Failed to parse the message: {data}")
            if parsed_data.comm_id not in session_manager.sessions:
            # 判断会话ID是否存在于该会话中
                logger.error(f"Failed to find the session {parsed_data.comm_id} for {agent_name}")

            # 记录聊天记录
            record = chat_record_manager[parsed_data.comm_id]
            # 取出对应id的历史聊天记录
            record["chat_record"].append(parsed_data)
            # 加入新聊天记录
            chat_record_manager[parsed_data.comm_id] = record
            await send_to_frontend(json.loads(parsed_data.model_dump_json()), "message")
            # 发送消息到前端展示
            for receiver in session_manager.sessions[parsed_data.comm_id]:
                await connection_manager.send_personal_message(receiver, parsed_data)
                # 发送消息给会话中的每个agent,包括自己

    except WebSocketDisconnect:
        agent_name = quote(agent_name)
        await connection_manager.disconnect(agent_name)


@app.websocket("/chatlist_ws")
async def websocket_chatlist(websocket: WebSocket):
# 前端连接/chatlist_ws后将frontend_ws赋值为有效连接
    await websocket.accept()
    global frontend_ws
    frontend_ws = websocket

    try:
        while True:
            data = await websocket.receive_text()

    except WebSocketDisconnect:
        frontend_ws = None


# 测试接口
@app.post("/health_check")
async def health_check():
    return "ok"


# 注册接口
@app.post("/register")
async def register_agent(agent: AgentInfo):
    await agent_registry.register(agent)


# 检索接口
@app.post("/retrieve_assistant")
async def retrieve_assistant(
    characteristics: AgentRegistryRetrivalParam,
) -> list[AgentInfo]:
    agents = await agent_registry.retrieve(characteristics)
    return agents


# 精准查询接口
@app.post("/query_assistant")
async def query_assistant(
    param: AgentRegistryQueryParam,
) -> list[AgentInfo] | AgentInfo:
    agents = await agent_registry.query(param.name)
    return agents


# 组队会话创建窗口,并将信息传输至前端
@app.post("/teamup")
async def teamup(teamup_param: AgentRegistryTeamupParam):
    result = await session_manager.teamup(teamup_param.agent_names + [teamup_param.sender])
    result["team_name"] = teamup_param.team_name
    chat_record_manager[result["comm_id"]] = {
        "comm_id": result["comm_id"],
        "agent_names": result["agent_names"],
        "team_name": teamup_param.team_name,
        "chat_record": [],
    }
    await send_to_frontend(result, "teamup")
    return result


# 服务器向前端推送实时消息
async def send_to_frontend(data: dict, type: str):
    global frontend_ws
    if frontend_ws:
        try:
            new_result = deepcopy(data)
            new_result["frontend_type"] = type
            await frontend_ws.send_text(json.dumps(new_result))
        except WebSocketDisconnect:
            frontend_ws = None


# 查询系统中所有已注册Agent
@app.post("/list_all_agents")
async def list_all_agents():
    return agent_registry.agents.items()


# 查询会话聊天记录,若未传id则返回所有会话的聊天记录,且支持多个id查询
@app.post("/fetch_chat_record")
async def fetch_chat_record(param: ChatRecordFetchParam):
    if param.comm_id is None:
        return chat_record_manager.todict()
    if isinstance(param.comm_id, str):
        param.comm_id = [param.comm_id]
    return {comm_id: chat_record_manager[comm_id] for comm_id in param.comm_id}


# Milvus全局连接管理器,用于建立和管理 Python 程序与 Milvus 数据库之间的连接
connections.connect(
    alias="default",
    user="username",
    password="password",
    # host="localhost",
    host="standalone",
    port="19530",
)

agent_registry = AgentRegistry()
# Agent 注册表的全局实例
connection_manager = ConnectionManager()
# WebSocket 连接管理器的全局实例
session_manager = SessionManager()
# 会话管理器的全局实例
chat_record_manager = AutoStoredDict("database/server/chat.db", tablename="chat")
# 聊天记录管理器的全局实例
frontend_ws = None
# 存储前端与服务器建立的 WebSocket 连接对象

if __name__ == "__main__":
    # 启动 FastAPI 应用，监听所有可用的网络接口（ws_ping_timeout=None禁用 WebSocket 心跳超时检测,保持长连接）
    uvicorn.run(app, host="0.0.0.0", port=7788, ws_ping_timeout=None)
