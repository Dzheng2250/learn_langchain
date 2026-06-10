
from langchain_core.tools import tool

from src.core.common.debug import debug_print


@tool
def get_weather(city: str) -> str:
    """根据城市名称查询当前天气。"""
    debug_print("TOOL get_weather INPUT", f"city={city!r}")

    # 这里先用本地假数据模拟真实天气 API，后续可以替换成 HTTP 请求。
    weather_data = {
        "北京": "晴，18°C，北风 2 级",
        "上海": "多云，22°C，东南风 3 级",
        "深圳": "小雨，26°C，湿度较高",
        "香港": "阴，25°C，偶有阵雨",
    }
    result = weather_data.get(city, f"暂时没有 {city} 的天气数据。")

    debug_print("TOOL get_weather OUTPUT", result)
    return result
