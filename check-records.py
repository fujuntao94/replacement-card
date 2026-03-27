import requests
from datetime import datetime, timedelta
import json
from typing import Dict, Any, List

# -------------------------- 配置常量区 --------------------------
# 飞书开放平台基础域名（国内版）
FEISHU_BASE_URL = "https://open.feishu.cn"
# 飞书API请求超时时间（秒）
FEISHU_TIMEOUT = 20
# 获取租户访问凭证的API路径
TOKEN_API = "/open-apis/auth/v3/tenant_access_token/internal"
# 查询补卡权限的API路径
REMEDY_QUERY_API = "/open-apis/attendance/v1/user_task_remedys/query_user_allowed_remedys"
# 最多往前查询的天数
MAX_SEARCH_DAYS = 7

# 飞书考勤补卡业务码映射表
# 用于将接口返回的数字业务码转换为可读的中文说明
FEISHU_ATTENDANCE_CODES = {
    0: "成功",
    1226501: "当天没有异常考勤，无需补卡",
    1226502: "考勤组设置不允许补卡",
    1226503: "考勤组设置只允许补过去多少天的卡，超出可补卡日期",
    1226504: "超出补卡次数，当前周期的补卡次数已用完"
}

# -------------------------- 工具函数区 --------------------------
def get_today_date() -> str:
    """
    获取今日日期，格式化为YYYYMMDD字符串
    """
    return datetime.now().strftime("%Y%m%d")

def get_date_list(days: int) -> list:
    """
    生成从今天开始往前N天的日期列表（今天排第一，依次往前）
    """
    today = datetime.now()
    date_list = []
    for i in range(days):
        date_str = (today - timedelta(days=i)).strftime("%Y%m%d")
        date_list.append(date_str)
    return date_list

def parse_remedy_record(remedy_record: Dict[str, Any]) -> Dict[str, Any]:
    """
    解析飞书返回的原始补卡记录，转换为易读的结构化字典
    """
    # 打卡类型映射（飞书接口返回数字编码）
    work_type_map = {1: "上班", 2: "下班", -1: "未知类型"}
    # 打卡状态映射（飞书接口返回英文状态码）
    punch_status_map = {
        "Lack": "缺卡（需要补卡）",
        "Normal": "正常打卡",
        "Late": "迟到",
        "Early": "早退",
        "Absent": "旷工",
        "Unknown": "未知状态"
    }

    # 打卡序号（从0开始，需+1）
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
    """
    校验飞书接口必要参数的合法性
    """
    if not isinstance(app_id, str) or not app_id.strip():
        raise ValueError("APP_ID不合法：不能为空、不能是空白字符串")
    if not isinstance(app_secret, str) or not app_secret.strip():
        raise ValueError("APP_SECRET不合法：不能为空、不能是空白字符串")
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("USER_ID不合法：不能为空、不能是空白字符串")

def feishu_request(method: str, url: str, **kwargs) -> dict:
    """
    通用飞书API请求封装函数
    """
    try:
        response = requests.request(
            method=method,
            url=url,
            timeout=FEISHU_TIMEOUT,
            ** kwargs
        )
        # 尝试解析响应为JSON（无论HTTP状态码）
        try:
            result = response.json()
        except json.JSONDecodeError:
            raise Exception(f"API响应不是JSON格式：{response.text}")
        
        # 返回解析结果（由上层处理业务码）
        return result
    except requests.exceptions.RequestException as e:
        raise Exception(f"API请求网络失败：{str(e)}")

def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    """
    获取飞书租户级访问凭证（tenant_access_token）
    """
    # 先校验参数合法性
    validate_feishu_params(app_id, app_secret, "dummy")
    
    # 构造Token请求URL和参数
    api_url = f"{FEISHU_BASE_URL}{TOKEN_API}"
    payload = {
        "app_id": app_id.strip(),
        "app_secret": app_secret.strip()
    }
    
    # 发送Token请求
    result = feishu_request(
        method="POST",
        url=api_url,
        headers={"Content-Type": "application/json"},
        json=payload
    )
    
    # 处理Token请求结果
    if result.get("code") != 0:
        error_msg = f"Token获取失败 [码:{result['code']}]: {result['msg']}"
        if result["code"] == 10003:
            error_msg += "\n可能原因：\n1. APP_ID/APP_SECRET含空格或格式错误\n2. APP_ID不是以cli_开头\n3. 应用未配置为企业自建应用"
        raise Exception(error_msg)
    
    # 提取Token并校验非空
    tenant_token = result.get("tenant_access_token")
    if not tenant_token:
        raise Exception("Token获取成功，但返回的tenant_access_token为空")
    return tenant_token

def query_feishu_remedy(
    user_id: str,
    app_id: str,
    app_secret: str,
    remedy_date: str,
    tenant_access_token: str = ""
) -> Dict[str, Any]:
    """
    核心函数：查询指定员工指定日期的补卡权限
    """
    # 1. 参数合法性校验
    validate_feishu_params(app_id, app_secret, user_id)
    user_id = user_id.strip()
    app_id = app_id.strip()
    app_secret = app_secret.strip()
    
    # 2. Token处理：优先使用传入的Token，无则自动获取
    normalized_token = str(tenant_access_token).strip()
    if normalized_token:
        final_token = normalized_token
    else:
        final_token = get_tenant_access_token(app_id, app_secret)
    
    # 3. 构造补卡查询请求参数
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
    
    # 4. 发送补卡查询请求
    result = feishu_request(
        method="POST",
        url=api_url,
        headers=headers,
        params=params,
        json=payload
    )
    
    # 5. 解析补卡查询响应
    code = result.get("code")
    msg = result.get("msg", "未知信息")
    log_id = result.get("error", {}).get("log_id", "")
    
    # 处理已知业务码
    if code in FEISHU_ATTENDANCE_CODES:
        if code == 0:
            raw_data = result.get("data", {})
            allowed_remedys = raw_data.get("user_allowed_remedys", [])
            
            if not allowed_remedys:
                return {
                    "message": f"该用户{remedy_date}无补卡相关记录",
                    "data": None,
                    "result": "",
                    "code": 0,
                    "log_id": log_id
                }
            else:
                last_remedy = allowed_remedys[-1]
                parsed_result = parse_remedy_record(last_remedy)
                return {
                    "message": f"成功查询到{remedy_date}补卡记录",
                    "data": last_remedy,
                    "result": str(parsed_result),
                    "code": 0,
                    "log_id": log_id
                }
        else:
            return {
                "message": f"{FEISHU_ATTENDANCE_CODES[code]}（查询日期：{remedy_date}）",
                "data": None,
                "result": "",
                "code": code,
                "log_id": log_id
            }
    else:
        raise Exception(
            f"补卡查询失败 [业务码:{code}]: {msg} "
            f"| 查询日期:{remedy_date} "
            f"| log_id:{log_id}"
        )

# -------------------------- 核心入口函数 --------------------------
def main(
    user_id: str,
    app_id: str,
    app_secret: str,
    tenant_access_token: str = ""
) -> Dict[str, Any]:
    """
    对外统一调用入口：查询最近7天所有补卡记录，返回 data 数组
    返回字段 100% 保持原来的样子
    """
    try:
        # 生成最近7天日期：今天 → 昨天 → 前天...
        date_list = get_date_list(MAX_SEARCH_DAYS)
        all_data: List[Dict[str, Any]] = []  # 数组，存放7天所有结果
        all_results = []

        # 循环查询7天
        for date in date_list:
            res = query_feishu_remedy(
                user_id=user_id,
                app_id=app_id,
                app_secret=app_secret,
                remedy_date=date,
                tenant_access_token=tenant_access_token
            )

            # 有数据就加入数组
            if res["data"] is not None:
                all_data.append(res["data"])
                all_results.append(res["result"])

        # 返回原有格式，data 是数组
        return {
            "message": f"已查询最近{MAX_SEARCH_DAYS}天数据，共找到{len(all_data)}条补卡记录",
            "data": all_data,  # 数组格式
            "result": str(all_results),
            "code": 0,
            "log_id": ""
        }

    except ValueError as e:
        return {
            "message": f"参数校验失败：{str(e)}",
            "result": "",
            "data": [],
            "code": -1,
            "log_id": ""
        }
    except Exception as e:
        return {
            "message": f"查询补卡权限失败：{str(e)}",
            "result": "",
            "data": [],
            "code": -2,
            "log_id": ""
        }

# -------------------------- 测试入口 --------------------------
if __name__ == "__main__":
    TEST_CONFIG = {
        "USER_ID": "b5491ce9",
        "APP_ID": "cli_a93bb2cf4d789cc9",
        "APP_SECRET": "aFyVc072SUFfSir3WgwBCd678ShnbwWO",
        "TENANT_TOKEN": ""
    }

    result = main(
        user_id=TEST_CONFIG["USER_ID"],
        app_id=TEST_CONFIG["APP_ID"],
        app_secret=TEST_CONFIG["APP_SECRET"],
        tenant_access_token=TEST_CONFIG["TENANT_TOKEN"]
    )

    print("\n📝 最终处理结果：")
    print(json.dumps(result, ensure_ascii=False, indent=2))