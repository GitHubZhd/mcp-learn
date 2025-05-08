import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from typing import List

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from openai import AsyncOpenAI


class MCPClient:
    def __init__(self, model_name: str, base_url: str, api_key: str, server_sources: List[str]):
        """
        初始化 MCP 客户端，用于管理多个子进程服务器的工具调用。
        :param model_name: 使用的模型名称，例如 "deepseek-chat"。
        :param base_url: OpenAI 接口的基础地址，例如 "https://api.deepseek.com/v1"。
        :param api_key: OpenAI API 密钥，用于身份验证。
        :param file_paths: Python 脚本文件路径列表，每个脚本将作为独立的子进程服务器运行。
        """
        self.model_name = model_name
        self.server_sources = server_sources
        self.sessions = {}  # 存储每个服务器的会话：server_id -> session
        self.tool_mapping = {}  # 工具映射：prefixed_name -> (session, original_tool_name)
        self.exit_stack = AsyncExitStack()  # 用于管理多个异步上下文的资源
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    async def initialize_sessions(self):
        """
        初始化所有子进程服务器的会话，建立工具映射关系。
        为每个Python脚本创建一个子进程，并通过标准输入输出流与之通信。
        """
        for i, server_source in enumerate(self.server_sources):
            server_params = StdioServerParameters(
                command="python",
                args=[server_source],
                env=None
            )
            server_id = f"server{i}"
            # 创建标准输入输出流通信通道
            write, read = await self.exit_stack.enter_async_context(stdio_client(server_params))
            # 初始化客户端会话
            session = await self.exit_stack.enter_async_context(ClientSession(write, read))
            await session.initialize()
            # 存储会话实例
            self.sessions[server_id] = session
            # 获取服务器提供的工具列表并建立映射关系
            response = await session.list_tools()
            for tool in response.tools:
                prefixed_name = f"{server_id}_{tool.name}"  # 添加服务器前缀以区分不同服务器的同名工具
                self.tool_mapping[prefixed_name] = (session, tool.name)
            print(f"\n已连接到服务器 {server_id}，支持以下工具:", [tool.name for tool in response.tools])

    async def initialize_sessions_sse(self):
        """
        初始化所有SSE服务器的会话，建立工具映射关系。
        通过SSE连接与服务器建立通信，获取可用工具列表并建立映射。
        """
        for i, server_source in enumerate(self.server_sources):
            server_id = f"server{i}"

            # 创建标准输入输出流通信通道
            write, read = await self.exit_stack.enter_async_context(sse_client(url=server_source))

            # 初始化客户端会话
            session = await self.exit_stack.enter_async_context(ClientSession(write, read))
            await session.initialize()

            # 存储会话实例
            self.sessions[server_id] = session

            # 获取服务器提供的工具列表并建立映射关系
            response = await session.list_tools()
            for tool in response.tools:
                prefixed_name = f"{server_id}_{tool.name}"  # 添加服务器前缀以区分不同服务器的同名工具
                self.tool_mapping[prefixed_name] = (session, tool.name)

            print(f"\n已连接到服务器 {server_id}，支持以下工具:", [tool.name for tool in response.tools])

    async def cleanup(self):
        """
        清理所有会话和连接资源
        """
        await self.exit_stack.aclose()

    async def chat_loop(self):
        """
        启动命令行交互式对话循环，处理用户输入并显示回复。
        支持通过输入'quit'退出对话。
        """
        print("\nMCP 客户端已启动，输入你的问题，输入 'quit' 退出。")
        while True:
            try:
                query = input("\n问题: ").strip()
                if query.lower() == "quit":
                    break
                response = await self.process_query(query)
                print("\n" + response)
            except Exception as e:
                print(f"\n发生错误: {str(e)}")

    async def process_query(self, query: str) -> str:
        """
        处理用户的自然语言查询，通过工具调用完成任务并返回结果。
        :param query: 用户输入的查询字符串
        :return: 处理后的回复文本，包含模型回复和工具调用结果
        """
        messages = [{"role": "user", "content": query}]  # 初始化对话消息列表
        # 收集所有可用工具的信息
        available_tools = []
        for server_id, session in self.sessions.items():
            response = await session.list_tools()
            for tool in response.tools:
                prefixed_name = f"{server_id}_{tool.name}"
                available_tools.append({
                    "type": "function",
                    "function": {
                        "name": prefixed_name,
                        "description": tool.description,
                        "parameters": tool.inputSchema,
                    },
                })
        # 向语言模型发送初始请求
        response = await self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            tools=available_tools,
        )
        final_text = []  # 存储所有回复内容
        message = response.choices[0].message
        final_text.append(message.content or "")  # 添加模型的初始回复
        # 处理模型请求的工具调用
        while message.tool_calls:
            for tool_call in message.tool_calls:
                prefixed_name = tool_call.function.name
                if prefixed_name in self.tool_mapping:
                    session, original_tool_name = self.tool_mapping[prefixed_name]
                    tool_args = json.loads(tool_call.function.arguments)
                    try:
                        # 执行工具调用
                        result = await session.call_tool(original_tool_name, tool_args)
                    except Exception as e:
                        result = {"content": f"调用工具 {original_tool_name} 出错：{str(e)}"}
                        print(result["content"])
                    final_text.append(f"[调用工具 {prefixed_name} 参数: {tool_args}]")
                    final_text.append(f"工具结果: {result.content}")
                    # 将工具调用结果添加到对话历史
                    messages.extend([
                        {
                            "role": "assistant",
                            "tool_calls": [{
                                "id": tool_call.id,
                                "type": "function",
                                "function": {"name": prefixed_name, "arguments": json.dumps(tool_args)},
                            }],
                        },
                        {"role": "tool", "tool_call_id": tool_call.id, "content": str(result.content)},
                    ])
                else:
                    print(f"工具 {prefixed_name} 未找到")
                    final_text.append(f"工具 {prefixed_name} 未找到")
            # 获取工具调用后的模型回复
            response = await self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                tools=available_tools,
            )
            message = response.choices[0].message
            if message.content:
                final_text.append(message.content)
        return "\n".join(final_text)


async def main():
    """
    程序入口点，负责：
    1. 从环境变量加载配置
    2. 初始化MCP客户端
    3. 启动交互式对话循环
    4. 确保资源正确清理
    """
    # 从环境变量获取配置
    model_name = os.getenv("MODEL_ID")
    base_url = os.getenv("OPENAI_BASE_URL")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("未设置 API_KEY 环境变量。")
        sys.exit(1)
    # 定义要启动的Python脚本文件列表
    server_sources = ["D:/ZQY/mcp-server-weather/mcp-server/amap_server.py"]
    # 创建并运行客户端
    client = MCPClient(model_name=model_name, base_url=base_url, api_key=api_key, server_sources=server_sources)
    try:
        await client.initialize_sessions()
        await client.chat_loop()
    finally:
        await client.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
