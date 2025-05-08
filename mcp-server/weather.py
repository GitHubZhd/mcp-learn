from typing import Any
import httpx
from mcp.server.fastmcp import FastMCP
# 初始化 FastMCP 服务器
mcp = FastMCP("weather")
# 常量
NWS_API_BASE = "https://api.weather.gov"
USER_AGENT = "weather-app/1.0"


async def make_nws_request(url: str) -> dict[str, Any] | None:
    """向 NWS API 发送请求，并进行错误处理"""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/geo+json"
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, timeout=30.0)
            response.raise_for_status()
            return response.json()
        except Exception:
            return None


def format_alert(feature: dict) -> str:
    """将天气警报格式化为可读的字符串"""
    props = feature["properties"]
    return f"""
            事件: {props.get('event', '未知')}
            地区: {props.get('areaDesc', '未知')}
            严重程度: {props.get('severity', '未知')}
            描述: {props.get('description', '无描述')}
            指引: {props.get('instruction', '无具体指引')}
            """


@mcp.tool()
async def get_alerts(state: str) -> str:
    """获取某个美国州的天气警报。
    参数:
        state: 两个字母的州代码（如 CA、NY）
    """
    url = f"{NWS_API_BASE}/alerts/active/area/{state}"
    data = await make_nws_request(url)
    if not data or "features" not in data:
        return "无法获取警报或未找到警报信息。"
    if not data["features"]:
        return "该州当前没有活跃的天气警报。"
    alerts = [format_alert(feature) for feature in data["features"]]
    return "\n---\n".join(alerts)


@mcp.tool()
async def get_forecast(latitude: float, longitude: float) -> str:
    """获取指定位置的天气预报。
    参数:
        latitude: 纬度
        longitude: 经度
    """
    # 获取天气预报的 API 端点
    points_url = f"{NWS_API_BASE}/points/{latitude},{longitude}"
    points_data = await make_nws_request(points_url)
    if not points_data:
        return "无法获取该位置的天气预报数据。"
    # 获取天气预报 URL
    forecast_url = points_data["properties"]["forecast"]
    forecast_data = await make_nws_request(forecast_url)
    if not forecast_data:
        return "无法获取详细的天气预报信息。"
    # 格式化天气预报
    periods = forecast_data["properties"]["periods"]
    forecasts = []
    for period in periods[:5]:  # 仅显示最近 5 个时段的预报
        forecast = f"""
                    {period['name']}:
                    温度: {period['temperature']}°{period['temperatureUnit']}
                    风速: {period['windSpeed']} {period['windDirection']}
                    天气: {period['detailedForecast']}
                    """
        forecasts.append(forecast)
    return "\n---\n".join(forecasts)


if __name__ == "__main__":
    mcp.run(transport='stdio')
