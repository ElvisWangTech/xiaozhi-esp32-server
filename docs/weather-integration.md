# 天气插件使用指南

## 概述

天气插件 `get_weather` 是小智ESP32语音助手的核心功能之一，支持通过语音查询全国各地的天气信息。插件基于和风天气API，提供实时天气和7天天气预报功能。

## API Key 申请指南

### 1. 注册和风天气账号

1. 访问 [和风天气控制台](https://console.qweather.com/)
2. 注册账号并完成邮箱验证
3. 登录控制台

### 2. 创建应用获取API Key

1. 进入控制台后，点击右侧["项目管理"](https://console.qweather.com/project?lang=zh) → "创建项目"
2. 填写项目信息：
   - **项目名称**：如"小智语音助手"
3. 点击保存
4. 项目创建完成后，在该项目中点击"创建凭据"
5. 填写凭据信息：
    - **凭据名称**：如"小智语音助手"
    - **身份认证方式**：选择"API Key"
6. 点击保存
7. 在凭据中复制`API Key`，这是第一个关键的配置信息

### 3. 获取API Host

1. 在控制台中点击["设置"](https://console.qweather.com/setting?lang=zh) → "API Host"
2. 查看分配给你的专属`API Host`地址，这个是第二个关键的配置信息

以上操作，会得到两个重要的配置信息:`API Key`和`API Host`

## 配置方式(任选一种)

### 方式1. 如果你使用了智控台部署（推荐）

天气查询的 API Host、API Key 和默认城市配在系统参数里。保存后，每个开启了意图识别的角色都会自动继承，不用再进角色的「编辑功能」单独填写。默认已经开启。

1. 登录智控台
2. 进入「参数管理」
3. 打开「插件 · 天气查询」
4. 修改下面几项后保存：
   - `plugins.get_weather.enabled`：总开关，默认 `true`
   - `plugins.get_weather.api_host`：和风天气 API Host
   - `plugins.get_weather.api_key`：和风天气 API Key
   - `plugins.get_weather.default_location`：定位不到城市时使用的默认城市，默认 `上海`

角色需要使用函数调用或意图大模型。意图识别设为「不使用意图识别」的角色不会加载插件。

设备下次建立对话即生效，不用逐个保存角色。某个角色如果在「编辑功能」里填了自己的 API Key，则该角色继续用自己的配置。没填，或仍是项目共用密钥的角色，使用系统参数。将 `plugins.get_weather.enabled` 设为 `false` 后，这些角色不再加载天气查询。

### 方式2. 如果你只是单模块xiaozhi-server部署

在 `data/.config.yaml` 中配置：

1. 将复制过来的第一个关键配置`API Key`,填入到`api_key`里
2. 将复制过来的第二个关键配置`API Host`,填入到`api_host`里
3. 将你所在的城市填入到`default_location`里，例如`广州`

```yaml
plugins:
  get_weather:
    api_key: "你的和风天气API密钥"
    api_host: "你的和风天气API主机地址"
    default_location: "你的默认查询城市"
```

