from datetime import datetime
from time import perf_counter

import requests
from flask import Flask, jsonify, request

try:
    from config_local import Config
except ImportError:
    from config import Config


app = Flask(__name__)


class AppError(Exception):
    """应用级异常，统一返回 JSON 错误结构。"""

    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def format_wechat_report(text):
    """把豆包输出整理成更适合公众号文本消息阅读的格式。"""
    if not text:
        return text

    lines = [line.strip() for line in text.replace("\r\n", "\n").split("\n")]
    lines = [line for line in lines if line]

    if not lines:
        return text

    # 强制使用服务端当天日期作为标题，避免模型自行编造日期。
    now = datetime.now()
    title = f"金乡大蒜行情简报（{now.year}年{now.month}月{now.day}日）"

    normalized = [title, ""]
    section_map = {
        "今日报价": "【今日报价】",
        "行情变化原因": "【行情变化原因】",
        "后续走势判断": "【后续走势判断】",
        "风险提示": "【风险提示】",
        "简要结论": "【简要结论】",
    }

    current_section = None
    for raw_line in lines[1:]:
        line = raw_line.replace("：", "").replace(":", "").strip()

        matched_section = None
        for key, section_title in section_map.items():
            if key in line:
                matched_section = section_title
                break

        if matched_section:
            if normalized and normalized[-1] != "":
                normalized.append("")
            normalized.append(matched_section)
            current_section = matched_section
            continue

        # 把今日报价部分保持成一行一个报价，方便手机阅读。
        if current_section == "【今日报价】":
            quote_line = raw_line.strip()
            quote_line = quote_line.replace("（", "").replace("）", "")
            if "全文共" not in quote_line:
                normalized.append(quote_line)
            continue

        # 其他部分保留原始句子，但避免出现连续空行。
        normal_line = raw_line.strip()
        if "全文共" not in normal_line:
            normalized.append(normal_line)

    # 清理首尾和多余空行
    cleaned = []
    prev_blank = False
    for line in normalized:
        is_blank = line == ""
        if is_blank and prev_blank:
            continue
        cleaned.append(line)
        prev_blank = is_blank

    return "\n".join(cleaned).strip()


def validate_doubao_config():
    """校验豆包必要配置。"""
    if not Config.DOUBAO_API_KEY:
        raise AppError("DOUBAO_API_KEY is not configured", 500)
    if not Config.DOUBAO_MODEL:
        raise AppError("DOUBAO_MODEL is not configured", 500)


def validate_gzh_config():
    """校验公众号必要配置。"""
    if not Config.WX_GZH_APPID:
        raise AppError("WX_GZH_APPID is not configured", 500)
    if not Config.WX_GZH_APPSECRET:
        raise AppError("WX_GZH_APPSECRET is not configured", 500)


def call_doubao(question):
    """使用默认 Prompt 调用豆包。"""
    return call_doubao_with_prompt(Config.SYSTEM_PROMPT, question)


def call_doubao_with_prompt(system_prompt, question):
    """按指定 Prompt 调用豆包，并返回文本结果与耗时。"""
    started_at = perf_counter()
    headers = {
        "Authorization": f"Bearer {Config.DOUBAO_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": Config.DOUBAO_MODEL,
        "input": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": system_prompt,
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": question,
                    }
                ],
            },
        ],
        "temperature": Config.DOUBAO_TEMPERATURE,
        "max_output_tokens": Config.DOUBAO_MAX_OUTPUT_TOKENS,
        "thinking": {"type": "disabled"},
    }

    try:
        response = requests.post(
            Config.DOUBAO_API_URL,
            headers=headers,
            json=payload,
            timeout=Config.DOUBAO_TIMEOUT_SECONDS,
        )
    except requests.Timeout as exc:
        raise AppError("Doubao API request timed out", 504) from exc
    except requests.RequestException as exc:
        raise AppError(f"Doubao API request failed: {exc}", 502) from exc

    if not response.ok:
        response_text = response.text.strip()
        detail = f": {response_text}" if response_text else ""
        raise AppError(
            f"Doubao API failed with status {response.status_code}{detail}",
            502,
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise AppError("Failed to parse Doubao API response", 502) from exc

    # 优先读取官方直出的 output_text。
    if data.get("output_text"):
        return data["output_text"], round((perf_counter() - started_at) * 1000)

    # 兼容 output -> content 这种嵌套结构。
    output = data.get("output", [])
    for item in output:
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                return content["text"], round((perf_counter() - started_at) * 1000)
        # 某些接入点可能只返回 summary，做一层兜底兼容。
        for summary in item.get("summary", []):
            if summary.get("type") in {"summary_text", "output_text", "text"} and summary.get("text"):
                return summary["text"], round((perf_counter() - started_at) * 1000)

    raise AppError("No text content found in Doubao response", 502)


def push_to_qywx(ai_result):
    """企业微信 webhook 推送，当前仅作兼容保留。"""
    if not Config.WECHAT_WEBHOOK_URL:
        return False

    payload = {
        "msgtype": "text",
        "text": {
            "content": ai_result,
        },
    }

    try:
        response = requests.post(
            Config.WECHAT_WEBHOOK_URL,
            json=payload,
            timeout=30,
        )
    except requests.Timeout as exc:
        raise AppError("WeChat webhook request timed out", 504) from exc
    except requests.RequestException as exc:
        raise AppError(f"WeChat webhook request failed: {exc}", 502) from exc

    if not response.ok:
        raise AppError(
            f"WeChat webhook failed with status {response.status_code}",
            502,
        )

    try:
        result = response.json()
    except ValueError as exc:
        raise AppError("Failed to parse WeChat webhook response", 502) from exc

    if result.get("errcode") != 0:
        raise AppError(f"WeChat push failed: {result.get('errmsg', 'unknown error')}", 502)

    return True


def get_gzh_access_token():
    """获取微信公众号 access_token。"""
    validate_gzh_config()

    try:
        response = requests.get(
            f"{Config.WX_GZH_API_BASE}/cgi-bin/token",
            params={
                "grant_type": "client_credential",
                "appid": Config.WX_GZH_APPID,
                "secret": Config.WX_GZH_APPSECRET,
            },
            timeout=30,
        )
    except requests.Timeout as exc:
        raise AppError("WeChat Official Account token request timed out", 504) from exc
    except requests.RequestException as exc:
        raise AppError(f"WeChat Official Account token request failed: {exc}", 502) from exc

    if not response.ok:
        raise AppError(
            f"WeChat Official Account token request failed with status {response.status_code}",
            502,
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise AppError("Failed to parse WeChat Official Account token response", 502) from exc

    access_token = data.get("access_token")
    if not access_token:
        raise AppError(
            f"WeChat Official Account token fetch failed: {data.get('errmsg', data)}",
            502,
        )

    return access_token


def broadcast_gzh_text(content):
    """调用公众号 sendall 接口，对全体粉丝群发纯文本消息。"""
    access_token = get_gzh_access_token()
    payload = {
        "filter": {
            "is_to_all": True,
        },
        "text": {
            "content": content,
        },
        "msgtype": "text",
    }

    try:
        response = requests.post(
            f"{Config.WX_GZH_API_BASE}/cgi-bin/message/mass/sendall",
            params={"access_token": access_token},
            json=payload,
            timeout=60,
        )
    except requests.Timeout as exc:
        raise AppError("WeChat Official Account broadcast request timed out", 504) from exc
    except requests.RequestException as exc:
        raise AppError(f"WeChat Official Account broadcast request failed: {exc}", 502) from exc

    if not response.ok:
        raise AppError(
            f"WeChat Official Account broadcast failed with status {response.status_code}",
            502,
        )

    try:
        result = response.json()
    except ValueError as exc:
        raise AppError("Failed to parse WeChat Official Account broadcast response", 502) from exc

    if result.get("errcode") != 0:
        raise AppError(
            f"WeChat Official Account broadcast failed: {result.get('errmsg', result)}",
            502,
        )

    return result


@app.errorhandler(AppError)
def handle_app_error(error):
    """统一业务错误返回。"""
    return jsonify({"success": False, "error": error.message}), error.status_code


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    """统一兜底异常返回。"""
    return jsonify({"success": False, "error": f"Unexpected error: {error}"}), 500


@app.route("/ask", methods=["POST"])
def ask():
    """接口1：仅调用豆包，返回分析结果。"""
    validate_doubao_config()

    data = request.get_json(silent=True) or {}
    question = str(data.get("question", "")).strip()

    if not question:
        raise AppError("question cannot be empty", 400)

    ai_result, duration_ms = call_doubao(question)
    ai_result = format_wechat_report(ai_result) if "金乡大蒜" in question else ai_result
    pushed = push_to_qywx(ai_result)

    return jsonify(
        {
            "success": True,
            "question": question,
            "result": ai_result,
            "pushed": pushed,
            "duration_ms": duration_ms,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )


@app.route("/gzh/broadcast", methods=["POST"])
def gzh_broadcast():
    """接口2：公众号文本群发测试接口。"""
    data = request.get_json(silent=True) or {}
    content = str(data.get("content", "")).strip()
    dry_run = bool(data.get("dry_run", True))

    if not content:
        raise AppError("content cannot be empty", 400)

    if dry_run:
        access_token = get_gzh_access_token()
        return jsonify(
            {
                "success": True,
                "dry_run": True,
                "content": content,
                "access_token_ok": bool(access_token),
                "request_payload": {
                    "filter": {"is_to_all": True},
                    "text": {"content": content},
                    "msgtype": "text",
                },
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    result = broadcast_gzh_text(content)
    return jsonify(
        {
            "success": True,
            "content": content,
            "dry_run": False,
            "wechat_result": result,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )


@app.route("/gzh/report/push", methods=["POST"])
def gzh_report_push():
    """接口3：固定问题 -> 豆包生成报告 -> 公众号纯文本群发。"""
    validate_doubao_config()
    validate_gzh_config()

    data = request.get_json(silent=True) or {}
    dry_run = bool(data.get("dry_run", True))

    question = Config.FIXED_GARLIC_REPORT_QUESTION
    ai_result, duration_ms = call_doubao_with_prompt(Config.GZH_REPORT_PROMPT, question)
    ai_result = format_wechat_report(ai_result)

    if dry_run:
        access_token = get_gzh_access_token()
        return jsonify(
            {
                "success": True,
                "dry_run": True,
                "question": question,
                "result": ai_result,
                "duration_ms": duration_ms,
                "access_token_ok": bool(access_token),
                "request_payload": {
                    "filter": {"is_to_all": True},
                    "text": {"content": ai_result},
                    "msgtype": "text",
                },
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    result = broadcast_gzh_text(ai_result)
    return jsonify(
        {
            "success": True,
            "dry_run": False,
            "question": question,
            "result": ai_result,
            "duration_ms": duration_ms,
            "wechat_result": result,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
