import json
import os
import requests
from dotenv import load_dotenv, find_dotenv
from mcp.server.fastmcp import FastMCP

_ = load_dotenv(find_dotenv())
mcp = FastMCP("WeatherServer", port=9999)


@mcp.tool()
async def get_weather(city: str) -> list[dict]:
    """调用高德天气API获取天气预报信息

    Args:
        city: 城市名称 (如"福州")

    Returns:
        包含未来天气预报信息的字典列表，格式示例(week:7 星期日)：
        [{
            'date': 'yyyy-MM-dd',
            'dayweather': '晴',
            'nightweather': '多云',
            'daytemp': '25',
            'nighttemp': '15',
            'daywind': '东北',
            'nightwind': '东北',
            'daypower': '4',
            'nightpower': '3'
        }]

    Raises:
        ValueError: 当参数无效或API返回错误时
        RequestException: 当网络请求失败时
    """
    # 参数验证
    if not city:
        raise ValueError("city参数不能为空")

    api_key = os.getenv("AMAP_MAPS_API_KEY")

    if not api_key:
        raise ValueError("未找到高德地图API密钥，请检查环境变量AMAP_MAPS_API_KEY")

    # 构造请求参数
    params = {
        'key': api_key,
        'city': city,
        'extensions': 'all',
        'output': 'json'
    }
    url = 'https://restapi.amap.com/v3/weather/weatherInfo'

    try:
        # 发送请求
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        # 验证API响应
        if data.get('status') != '1':
            raise ValueError(f"API错误:{data.get('info','未知错误')}")
        if not data.get('forecasts'):
            raise ValueError("未获取到天气预报数据")

        # 解析返回数据
        casts = data['forecasts'][0]['casts']
        result = []
        for cast in casts:
            result.append({
                'date': cast.get('date'),
                'dayweather': cast.get('dayweather'),
                'nightweather': cast.get('nightweather'),
                'daytemp': cast.get('daytemp'),
                'nighttemp': cast.get('nighttemp'),
                'daywind': cast.get('daywind'),
                'nightwind': cast.get('nightwind'),
                'daypower': cast.get('daypower'),
                'nightpower': cast.get('nightpower')
            })
        return result
    except requests.exceptions.RequestException as e:
        raise requests.exceptions.RequestException(f"网络请求失败:{str(e)}")
    except json.JSONDecodeError as e:
        raise ValueError(f"API响应格式错误:{str(e)}")

if __name__ == "__main__":
    mcp.run(transport="stdio")
