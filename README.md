# AI Relay Hub（豆包版）

这是一个基于 Flask 的轻量服务，用来完成下面这条链路：

外部接口调用 -> 豆包分析 -> 生成适合公众号阅读的文本 -> 微信公众号群发 -> 返回 JSON

## 配置方式

当前项目配置分为两层：

[config.py](/E:/workspace/demo/ReportFlow/config.py)

和本地专用的：

`config_local.py`

规则如下：

- `config.py`：仓库版本，放占位符配置，可以提交到 GitHub
- `config_local.py`：本地真实配置，不提交到 GitHub

仓库里的 `config.py` 包括：

- 豆包 API Key
- 豆包接口地址
- 豆包接入点 ID
- 微信公众号 AppID
- 微信公众号 AppSecret
- 固定问题
- Prompt

当前版本不依赖 `.env`，本地直接用 `config_local.py` 即可。

## 本地配置方式

首次使用时，在项目根目录新建：

`config_local.py`

内容可参考：

```python
from config import Config as BaseConfig


class Config(BaseConfig):
    DOUBAO_API_KEY = "你的豆包 Key"
    DOUBAO_MODEL = "你的 Endpoint ID"
    WX_GZH_APPID = "你的公众号 AppID"
    WX_GZH_APPSECRET = "你的公众号 AppSecret"
```

这个文件已经加入 `.gitignore`，不会被提交。

## 安装依赖

```bash
pip install -r requirements.txt
```

或：

```bash
pip install flask requests
```

## 启动服务

```bash
python app.py
```

服务地址：

```text
http://localhost:5000
```

## 接口说明

### 1. 豆包测试接口

`POST /ask`

作用：

- 只调用豆包
- 返回分析结果
- 不做公众号群发

请求示例：

```json
{
  "question": "今天金乡大蒜行情怎么样？"
}
```

### 2. 公众号手动群发测试接口

`POST /gzh/broadcast`

作用：

- 手动输入一段文本
- 走微信公众号群发
- 支持 `dry_run`

Dry run 示例：

```json
{
  "content": "这是一条公众号群发测试消息",
  "dry_run": true
}
```

正式发送示例：

```json
{
  "content": "这是一条公众号群发测试消息",
  "dry_run": false
}
```

### 3. 固定问题 -> 豆包生成 -> 公众号群发

`POST /gzh/report/push`

作用：

- 使用固定问题
- 调豆包生成适合公众号阅读的纯文本报告
- 直接走公众号群发
- 支持 `dry_run`

接口内部固定问题：

```text
帮我收集今天金乡大蒜的行情(报价)信息，同时预测后续行情走势，汇总成一份800字左右的报告
```

Dry run 示例：

```json
{
  "dry_run": true
}
```

正式发送示例：

```json
{
  "dry_run": false
}
```

## 为什么不用 Markdown

当前公众号发送用的是纯文本群发接口。

所以这版建议使用：

- 纯文本标题
- 纯文本分段
- 不使用 `#`、`*`、`-` 这类 Markdown 符号

这样在公众号文本消息里最稳，排版也更容易控制。

## 返回示例

成功：

```json
{
  "success": true,
  "result": "报告内容",
  "duration_ms": 12345,
  "timestamp": "2026-04-13 14:30:00"
}
```

失败：

```json
{
  "success": false,
  "error": "error message"
}
```

## 使用建议

推荐测试顺序：

1. 先测 `/ask`，确认豆包输出正常
2. 再测 `/gzh/broadcast` 的 `dry_run`
3. 再测 `/gzh/report/push` 的 `dry_run`
4. 最后确认内容没问题后，再把 `dry_run` 改成 `false`

## 注意事项

- 微信公众号后台需要先配置 IP 白名单
- `dry_run=true` 不会真正发消息
- 正式群发前建议先看 dry run 返回内容
