import requests
from datetime import datetime
import json
from typing import Dict, Any

# -------------------------- 配置常量 --------------------------
FEISHU_BASE_URL = "https://open.feishu.cn"
FEISHU_TIMEOUT = 20
TOKEN_API = "/open-apis/auth/v3/tenant_access_token/internal"
REMEDY_QUERY_API = "/open-apis/attendance/v1/user_task_remedys/query_user_allowed_remedys"

# 飞书考勤业务码映射（重点）
FEISHU_ATTENDANCE_CODES = {
    0: "成功",
    1226501: "当天没有异常考勤，无需补卡",
    1226502: "考勤组设置不允许补卡",
    1226503: "考勤组设置只允许补过去多少天的卡，超出可补卡日期",
    1226504: "超出补卡次数，当前周期的补卡次数已用完"
}

# -------------------------- 工具函数 --------------------------
def get_today_date() -> str:
    """获取今日日期，格式：20260311"""
    return datetime.now().strftime("%Y%m%d")

def parse_remedy_record(remedy_record: Dict[str, Any]) -> Dict[str, Any]:
    """解析补卡记录"""
    work_type_map = {1: "上班", 2: "下班", -1: "未知类型"}
    punch_status_map = {
        "Lack": "缺卡（需要补卡）",
        "Normal": "正常打卡",
        "Late": "迟到",
        "Early": "早退",
        "Absent": "旷工",
        "Unknown": "未知状态"
    }

    punch_no = remedy_record.get("punch_no", 0)
    return {
        "员工ID": remedy_record.get("user_id", "未知"),
        "补卡日期": str(remedy_record.get("remedy_date", "未知")),
        "是否免打卡": "是" if remedy_record.get("is_free_punch", False) else "否",
        "打卡序号": f"第{punch_no + 1}次上下班",
        "打卡类型": work_type_map.get(remedy_record.get("work_type", -1), f"未知类型({remedy_record.get('work_type')})"),
        "打卡状态": punch_status_map.get(remedy_record.get("punch_status"), f"未知状态({remedy_record.get('punch_status')})"),
        "正常打卡时间": remedy_record.get("normal_punch_time", "未知"),
        "补卡开始时间": remedy_record.get("remedy_start_time", "未知"),
        "补卡结束时间": remedy_record.get("remedy_end_time", "未知"),
        "核心结论": "需要补卡" if remedy_record.get("punch_status") == "Lack" else "无需补卡",
    }

def validate_feishu_params(app_id: str, app_secret: str, user_id: str) -> None:
    """校验飞书参数合法性"""
    if not isinstance(app_id, str) or not app_id.strip():
        raise ValueError("APP_ID不合法：不能为空、不能是空白字符串")
    if not isinstance(app_secret, str) or not app_secret.strip():
        raise ValueError("APP_SECRET不合法：不能为空、不能是空白字符串")
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("USER_ID不合法：不能为空、不能是空白字符串")

def feishu_request(method: str, url: str, **kwargs) -> dict:
    """重构：通用飞书API请求函数（不主动抛HTTP异常，先解析响应内容）"""
    try:
        response = requests.request(
            method=method,
            url=url,
            timeout=FEISHU_TIMEOUT,** kwargs
        )
        # 先尝试解析响应内容（无论HTTP状态码是否为200）
        try:
            result = response.json()
        except json.JSONDecodeError:
            raise Exception(f"API响应不是JSON格式：{response.text}")
        
        # 即使HTTP状态码非200，也先返回解析后的结果（让上层处理业务码）
        return result
    except requests.exceptions.RequestException as e:
        raise Exception(f"API请求网络失败：{str(e)}")

def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    """获取租户token"""
    validate_feishu_params(app_id, app_secret, "dummy")
    
    api_url = f"{FEISHU_BASE_URL}{TOKEN_API}"
    payload = {
        "app_id": app_id.strip(),
        "app_secret": app_secret.strip()
    }
    
    result = feishu_request(
        method="POST",
        url=api_url,
        headers={"Content-Type": "application/json"},
        json=payload
    )
    
    if result.get("code") != 0:
        error_msg = f"Token获取失败 [码:{result['code']}]: {result['msg']}"
        if result["code"] == 10003:
            error_msg += "\n可能原因：\n1. APP_ID/APP_SECRET含空格或格式错误\n2. APP_ID不是以cli_开头\n3. 应用未配置为企业自建应用"
        raise Exception(error_msg)
    
    tenant_token = result.get("tenant_access_token")
    if not tenant_token:
        raise Exception("Token获取成功，但返回的tenant_access_token为空")
    return tenant_token

def query_feishu_remedy(
    user_id: str,
    app_id: str,
    app_secret: str,
    tenant_access_token: str = ""
) -> Dict[str, Any]:
    """核心补卡查询函数（确保捕获1226501）"""
    # 1. 参数校验
    validate_feishu_params(app_id, app_secret, user_id)
    user_id = user_id.strip()
    app_id = app_id.strip()
    app_secret = app_secret.strip()
    
    # 2. 处理Token
    normalized_token = str(tenant_access_token).strip()
    if normalized_token:
        final_token = normalized_token
        print(f"✅ 使用传入的租户Token：{final_token[:10]}...（已脱敏）")
    else:
        final_token = get_tenant_access_token(app_id, app_secret)
        print(f"✅ 自动获取新的租户Token：{final_token[:10]}...（已脱敏）")
    
    # 3. 构造请求
    remedy_date = get_today_date()
    api_url = f"{FEISHU_BASE_URL}{REMEDY_QUERY_API}"
    
    headers = {
        "Authorization": f"Bearer {final_token}",
        "Content-Type": "application/json; charset=utf-8"
    }
    params = {"employee_type": "employee_id"}
    payload = {
        "user_id": user_id,
        "remedy_date": remedy_date
    }
    print(f"📤 请求参数：{json.dumps(payload, ensure_ascii=False)}")
    
    # 4. 发送请求（关键：这里不会因HTTP 400抛异常，先拿到完整响应）
    result = feishu_request(
        method="POST",
        url=api_url,
        headers=headers,
        params=params,
        json=payload
    )
    print(f"📥 接口原始返回：{json.dumps(result, ensure_ascii=False, indent=2)}")
    
    # 5. 处理响应（核心：优先判断业务码，不管HTTP状态码）
    code = result.get("code")
    msg = result.get("msg", "未知信息")
    
    # 处理已知业务码（包括1226501）
    if code in FEISHU_ATTENDANCE_CODES:
        if code == 0:
            raw_data = result.get("data", {})
            allowed_remedys = raw_data.get("user_allowed_remedys", [])
            if not allowed_remedys:
                return {
                    "message": "查询成功：该用户今日无补卡相关记录",
                    "data": None,
                    "result": "",
                    "code": 0,
                    "log_id": result.get("error", {}).get("log_id", "")
                }
            else:
                last_remedy = allowed_remedys[-1]
                parsed_result = parse_remedy_record(last_remedy)
                return {
                    "message": "成功查询到补卡记录",
                    "data": last_remedy,
                    "result": str(parsed_result),
                    "code": 0,
                    "log_id": result.get("error", {}).get("log_id", "")
                }
        else:
            return {
                "message": f"查询成功：{FEISHU_ATTENDANCE_CODES[code]}",
                "data": None,
                "result": "",
                "code": code,
                "log_id": result.get("error", {}).get("log_id", "")
            }
    # 处理未知业务码
    else:
        raise Exception(
            f"补卡查询失败 [业务码:{code}]: {msg} "
            f"| log_id:{result.get('error', {}).get('log_id', '无')}"
        )

# -------------------------- 核心入口函数 --------------------------
def main(
    user_id: str,
    app_id: str,
    app_secret: str,
    tenant_access_token: str = ""
) -> Dict[str, Any]:
    """对外调用入口"""
    try:
        return query_feishu_remedy(user_id, app_id, app_secret, tenant_access_token)
    except ValueError as e:
        return {
            "message": f"参数校验失败：{str(e)}",
            "result": "",
            "data": None,
            "code": -1,
            "log_id": ""
        }
    except Exception as e:
        return {
            "message": f"查询补卡权限失败：{str(e)}",
            "result": "",
            "data": None,
            "code": -2,
            "log_id": ""
        }

# -------------------------- 测试入口 --------------------------
if __name__ == "__main__":
    # 替换为你的真实参数
    TEST_CONFIG = {
        "USER_ID": "b5491ce9",
        "APP_ID": "cli_a93bb2cf4d789cc9",
        "APP_SECRET": "aFyVc072SUFfSir3WgwBCd678ShnbwWO",
        "TENANT_TOKEN": ""
    }

    # 执行查询
    result = main(
        user_id=TEST_CONFIG["USER_ID"],
        app_id=TEST_CONFIG["APP_ID"],
        app_secret=TEST_CONFIG["APP_SECRET"],
        tenant_access_token=TEST_CONFIG["TENANT_TOKEN"]
    )

    # 输出结果
    print("\n📝 最终处理结果：")
    print(json.dumps(result, ensure_ascii=False, indent=2))