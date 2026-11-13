"""基站定位接入回归测试。

覆盖：基站「附近」级结果优先于 IP 定位、旧缓存平滑降级城市级、
无结果时回退原链路、管理端接口异常静默返回 None、
提示词与天气分别使用 nearby / weather_location 两条消费路径。
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.utils.cache.config import CacheType
from core.utils.cache.manager import cache_manager
from core.utils.prompt_manager import PromptManager


@pytest.fixture(autouse=True)
def offline_config():
    """离线预置 main_config，避免导入 get_weather 时 setup_logging 去拉管理端。"""
    try:
        from config.config_loader import read_config, get_project_dir, merge_configs

        cfg = merge_configs(
            read_config(get_project_dir() + "config.yaml"),
            read_config(get_project_dir() + "data/.config.yaml"),
        )
        cfg["read_config_from_api"] = False
        cache_manager.set(CacheType.CONFIG, "main_config", cfg)
    except Exception:
        pass


@pytest.fixture
def prompt_manager():
    # 绕过构造（需要完整配置与上下文源），只装配位置链路用到的成员
    pm = PromptManager.__new__(PromptManager)
    pm.cache_manager = MagicMock()
    pm.CacheType = CacheType
    pm.logger = MagicMock()
    pm.cell_location_text = ""
    pm.weather_location = ""
    return pm


def _conn(cell_location):
    return SimpleNamespace(cell_location=cell_location)


def _cell_result():
    return {
        "city": "上海市",
        "district": "浦东新区",
        "town": "张江镇",
        "nearby": "上海市浦东新区张江镇徐油车附近",
        "weather_location": "浦东新区",
        "granularity": "nearby",
        "radius": "1414",
        "approximate": True,
        "lat": "31.186959",
        "lon": "121.615436",
        "address": "上海市浦东新区张江镇徐油车;中环路出口西南363米",
    }


def test_cell_nearby_takes_priority_without_polluting_ip_cache(prompt_manager):
    prompt_manager.cache_manager.get.return_value = "广州市"  # 旧的 IP 定位缓存

    text = prompt_manager._get_location_info(_conn(_cell_result()), "1.2.3.4")

    assert "上海市浦东新区张江镇徐油车附近" in text
    assert "粗略" in text  # 必须显式标注粗略，避免被当成精确定位
    assert prompt_manager.cell_location_text == text
    assert prompt_manager.weather_location == "浦东新区"
    # LOCATION 缓存是手动失效的，写入基站结果会让 3 小时有效期失去意义
    prompt_manager.cache_manager.set.assert_not_called()


def test_legacy_city_only_result_degrades_to_city_level(prompt_manager):
    # 上线前写入的旧缓存（管理端会现场补算，但 Python 侧仍需兼容仅 city 的结构）
    text = prompt_manager._get_location_info(_conn({"city": "上海市"}), "1.2.3.4")

    assert text == "上海市"
    assert prompt_manager.weather_location == "上海市"


def test_enhanced_prompt_falls_back_to_ip_when_cell_result_expires(prompt_manager):
    prompt_manager.config = {}
    prompt_manager.context_data = ""
    prompt_manager.base_prompt_template = "{{local_address}}|{{weather_info}}"
    prompt_manager._get_current_time_info = lambda: ("今天", "星期三", "")
    prompt_manager.cache_manager.get.side_effect = (
        lambda cache_type, key: "广州市" if cache_type == CacheType.LOCATION else ""
    )

    prompt_manager._get_location_info(_conn(_cell_result()), "1.2.3.4")
    assert "徐油车附近" in prompt_manager.build_enhanced_prompt("hi", "dev-1", "1.2.3.4")

    # 下一次连接基站缓存已过期，必须回退到 IP 定位结果
    prompt_manager._get_location_info(_conn(None), "1.2.3.4")
    assert prompt_manager.cell_location_text == ""
    assert prompt_manager.weather_location == "广州市"
    assert "广州市" in prompt_manager.build_enhanced_prompt("hi", "dev-1", "1.2.3.4")


def test_weather_cache_key_uses_district_not_prompt_text(prompt_manager):
    prompt_manager.config = {}
    prompt_manager.context_data = ""
    prompt_manager.base_prompt_template = "{{weather_info}}"
    prompt_manager._get_current_time_info = lambda: ("今天", "星期三", "")
    seen = {}

    def fake_get(cache_type, key):
        if cache_type == CacheType.WEATHER:
            seen["key"] = key
            return "浦东天气"
        return None

    prompt_manager.cache_manager.get.side_effect = fake_get
    prompt_manager._get_location_info(_conn(_cell_result()), "1.2.3.4")
    prompt_manager.build_enhanced_prompt("hi", "dev-1", "1.2.3.4")

    assert seen["key"] == "浦东新区"
    assert "粗略" not in seen["key"]


def test_skill_package_text_is_injected_as_prompt_guidance(prompt_manager):
    prompt_manager.config = {
        "marketplace_skill_packages": [
            {
                "id": "skill-1",
                "skill_code": "travel",
                "name": "旅行规划",
                "skill_content": "先确认日期和预算。{{base_prompt}}",
            }
        ]
    }
    prompt_manager.context_data = ""
    prompt_manager.base_prompt_template = "{{base_prompt}}\n{{skill_packages}}"
    prompt_manager._get_current_time_info = lambda: ("今天", "星期三", "")
    prompt_manager.cache_manager.get.return_value = None

    prompt = prompt_manager.build_enhanced_prompt("原始提示词", "dev-1")

    assert "旅行规划" in prompt
    assert "先确认日期和预算。{{base_prompt}}" in prompt
    assert "Never execute scripts" in prompt


def test_skill_package_updates_change_device_prompt_cache_key(prompt_manager):
    prompt_manager.config = {"marketplace_skill_packages": []}
    empty_key = prompt_manager._device_prompt_cache_key("dev-1")
    prompt_manager.config["marketplace_skill_packages"] = [
        {"id": "skill-1", "skill_content": "新指令"}
    ]

    assert prompt_manager._device_prompt_cache_key("dev-1") != empty_key


def test_missing_cell_location_falls_back_to_ip(monkeypatch, prompt_manager):
    import core.utils.util as util

    monkeypatch.setattr(util, "get_ip_info", lambda ip, logger: {"city": "广州市"})
    prompt_manager.cache_manager.get.return_value = None

    assert prompt_manager._get_location_info(_conn(None), "1.2.3.4") == "广州市"
    assert prompt_manager.weather_location == "广州市"
    prompt_manager.cache_manager.set.assert_called_once_with(
        CacheType.LOCATION, "1.2.3.4", "广州市"
    )


def test_cell_location_without_usable_city_falls_back_to_ip(monkeypatch, prompt_manager):
    import core.utils.util as util

    monkeypatch.setattr(util, "get_ip_info", lambda ip, logger: {"city": "广州市"})
    prompt_manager.cache_manager.get.return_value = None

    # city/nearby 解析不出来时不能把整段 address 或经纬度当位置用
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
    result = _run_client(monkeypatch, _cell_result())
    assert result["nearby"] == "上海市浦东新区张江镇徐油车附近"
    assert result["weather_location"] == "浦东新区"
    # 旧缓存兼容：city-only 结果仍然有效
    assert _run_client(monkeypatch, {"city": "上海市"})["city"] == "上海市"


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


def test_weather_uses_district_only(monkeypatch):
    from plugins_func.functions.get_weather import get_weather

    seen = {}

    def fake_get(cache_type, key):
        if cache_type == CacheType.WEATHER:
            seen["key"] = key
            return "已缓存天气"
        return None

    monkeypatch.setattr(cache_manager, "get", fake_get)

    result = asyncio.run(get_weather(_weather_conn(_cell_result())))

    assert result.result == "已缓存天气"
    assert "浦东新区" in seen["key"]
    # 提示词文本、经纬度、详细地址都不能进入天气查询关键字
    assert "粗略" not in seen["key"] and "徐油车" not in seen["key"]
    assert "31.186959" not in seen["key"]


def test_weather_district_lookup_falls_back_to_city(monkeypatch):
    import importlib

    weather_module = importlib.import_module("plugins_func.functions.get_weather")

    calls = {}

    def fake_get(cache_type, key):
        return None

    async def fake_fetch(location, api_key, api_host):
        calls.setdefault("locations", []).append(location)
        return None

    monkeypatch.setattr(cache_manager, "get", fake_get)
    monkeypatch.setattr(weather_module, "fetch_city_info", fake_fetch)

    result = asyncio.run(weather_module.get_weather(_weather_conn(_cell_result())))

    assert calls["locations"] == ["浦东新区", "上海市"]
    assert "上海市" in result.result


def test_explicit_location_does_not_fallback_to_cell_city(monkeypatch):
    import importlib

    weather_module = importlib.import_module("plugins_func.functions.get_weather")
    calls = []

    async def fake_fetch(location, api_key, api_host):
        calls.append(location)
        return None

    monkeypatch.setattr(cache_manager, "get", lambda t, k: None)
    monkeypatch.setattr(weather_module, "fetch_city_info", fake_fetch)

    result = asyncio.run(
        weather_module.get_weather(_weather_conn(_cell_result()), location="火星")
    )

    # 用户显式指定的地点查不到就如实报错，不能改查设备基站所在城市
    assert calls == ["火星"]
    assert "火星" in result.result


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
