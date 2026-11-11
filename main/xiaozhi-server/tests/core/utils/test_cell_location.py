"""基站定位接入回归测试。

覆盖：基站定位优先于 IP 定位、无基站结果时回退原链路、
管理端接口异常静默返回 None、天气定位只使用城市级粗略结果。
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.utils.cache.config import CacheType
from core.utils.cache.manager import cache_manager
from core.utils.prompt_manager import PromptManager


@pytest.fixture
def prompt_manager():
    # 绕过构造（需要完整配置与上下文源），只装配 _get_location_info 用到的成员
    pm = PromptManager.__new__(PromptManager)
    pm.cache_manager = MagicMock()
    pm.CacheType = CacheType
    pm.logger = MagicMock()
    return pm


def _conn(cell_location):
    return SimpleNamespace(cell_location=cell_location)


def test_cell_location_takes_priority_without_polluting_ip_cache(prompt_manager):
    prompt_manager.cache_manager.get.return_value = "广州市"  # 旧的 IP 定位缓存
    conn = _conn({"city": "上海市", "approximate": True, "lat": "31.186959"})

    assert prompt_manager._get_location_info(conn, "1.2.3.4") == "上海市"
    assert prompt_manager.cell_location_city == "上海市"
    # LOCATION 缓存是手动失效的，写入基站结果会让 3 小时有效期失去意义
    prompt_manager.cache_manager.set.assert_not_called()


def test_enhanced_prompt_falls_back_to_ip_when_cell_result_expires(prompt_manager):
    prompt_manager.config = {}
    prompt_manager.context_data = ""
    prompt_manager.base_prompt_template = "{{local_address}}"
    prompt_manager._get_current_time_info = lambda: ("今天", "星期三", "")
    prompt_manager.cache_manager.get.side_effect = (
        lambda cache_type, key: "广州市" if cache_type == CacheType.LOCATION else ""
    )

    prompt_manager._get_location_info(_conn({"city": "上海市"}), "1.2.3.4")
    assert "上海市" in prompt_manager.build_enhanced_prompt("hi", "dev-1", "1.2.3.4")

    # 下一次连接基站缓存已过期，必须回退到 IP 定位结果
    prompt_manager._get_location_info(_conn(None), "1.2.3.4")
    assert prompt_manager.cell_location_city == ""
    assert "广州市" in prompt_manager.build_enhanced_prompt("hi", "dev-1", "1.2.3.4")


def test_missing_cell_location_falls_back_to_ip(monkeypatch, prompt_manager):
    import core.utils.util as util

    monkeypatch.setattr(util, "get_ip_info", lambda ip, logger: {"city": "广州市"})
    prompt_manager.cache_manager.get.return_value = None

    assert prompt_manager._get_location_info(_conn(None), "1.2.3.4") == "广州市"
    prompt_manager.cache_manager.set.assert_called_once_with(
        CacheType.LOCATION, "1.2.3.4", "广州市"
    )


def test_cell_location_without_city_falls_back_to_ip(monkeypatch, prompt_manager):
    import core.utils.util as util

    monkeypatch.setattr(util, "get_ip_info", lambda ip, logger: {"city": "广州市"})
    prompt_manager.cache_manager.get.return_value = None

    # city 解析不出来时不能把整段 address 或经纬度当城市用
    conn = _conn({"city": None, "address": "中环路出口西南363米", "lat": "31.18"})
    assert prompt_manager._get_location_info(conn, "1.2.3.4") == "广州市"


def _run_client(monkeypatch, response):
    from config import manage_api_client

    async def fake_request(method, endpoint, **kwargs):
        assert endpoint == "/config/device-cell-location"
        return response

    fake = MagicMock()
    fake._execute_async_request = fake_request
    monkeypatch.setattr(
        manage_api_client.ManageApiClient,
        "_get_instance",
        classmethod(lambda cls: fake),
    )
    return asyncio.run(manage_api_client.get_device_cell_location("00:11:22:33:44:55"))


def test_client_returns_cell_location(monkeypatch):
    result = _run_client(monkeypatch, {"city": "上海市", "approximate": True})
    assert result["city"] == "上海市"
    assert result["approximate"] is True


def test_client_returns_none_on_incomplete_or_failed_response(monkeypatch):
    assert _run_client(monkeypatch, None) is None
    assert _run_client(monkeypatch, {"address": "无城市"}) is None

    from config import manage_api_client

    async def failing_request(method, endpoint, **kwargs):
        raise RuntimeError("manager-api 超时")

    fake = MagicMock()
    fake._execute_async_request = failing_request
    monkeypatch.setattr(
        manage_api_client.ManageApiClient,
        "_get_instance",
        classmethod(lambda cls: fake),
    )
    assert asyncio.run(
        manage_api_client.get_device_cell_location("00:11:22:33:44:55")
    ) is None


def _weather_conn(cell_location, client_ip="1.2.3.4"):
    return SimpleNamespace(config={}, client_ip=client_ip, cell_location=cell_location)


def test_weather_uses_cell_city_only(monkeypatch):
    from plugins_func.functions.get_weather import get_weather

    seen = {}

    def fake_get(cache_type, key):
        if cache_type == CacheType.WEATHER:
            seen["key"] = key
            return "已缓存天气"
        return None

    monkeypatch.setattr(cache_manager, "get", fake_get)

    conn = _weather_conn({"city": "上海市", "lat": "31.186959", "lon": "121.615436"})
    result = asyncio.run(get_weather(conn))

    assert result.result == "已缓存天气"
    assert "上海市" in seen["key"]
    # 粗略定位的经纬度不能进入天气查询关键字
    assert "31.186959" not in seen["key"]


def test_weather_falls_back_to_ip_when_no_cell_location(monkeypatch):
    from plugins_func.functions.get_weather import get_weather

    seen = {}

    def fake_get(cache_type, key):
        if cache_type == CacheType.IP_INFO:
            return {"city": "广州市"}
        if cache_type == CacheType.WEATHER:
            seen["key"] = key
            return "已缓存天气"
        return None

    monkeypatch.setattr(cache_manager, "get", fake_get)

    result = asyncio.run(get_weather(_weather_conn(None)))

    assert result.result == "已缓存天气"
    assert "广州市" in seen["key"]
