import requests
from datetime import datetime
import json
from typing import Dict, Any

# -------------------------- 配置常量区 --------------------------
# 飞书开放平台基础域名（国内版）
FEISHU_BASE_URL = "https://open.feishu.cn"
# 飞书API请求超时时间（秒）
FEISHU_TIMEOUT = 20
# 获取租户访问凭证的API路径
TOKEN_API = "/open-apis/auth/v3/tenant_access_token/internal"
# 查询补卡权限的API路径
REMEDY_QUERY_API = "/open-apis/attendance/v1/user_task_remedys/query_user_allowed_remedys"

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
    
    Returns:
        str: 格式化后的今日日期，例如"20260312"
    """
    return datetime.now().strftime("%Y%m%d")

def parse_remedy_date(day: Any) -> str:
    """
    解析补卡日期参数，处理各种输入情况
    
    Args:
        day: 补卡日期参数，支持数字/字符串/空值等类型
        
    Returns:
        str: 标准化的补卡日期（YYYYMMDD格式）
             - 传入有效日期 → 返回该日期
             - 传入无效/空值 → 返回今日日期
    """
    # 处理空值场景（None/空字符串/0等）
    if not day or str(day).strip() == "":
        return get_today_date()
    
    # 统一转换为字符串并去除首尾空格
    date_str = str(day).strip()
    
    # 校验是否为8位数字的合法日期格式
    if len(date_str) == 8 and date_str.isdigit():
        try:
            # 验证日期合法性（避免20260230这类无效日期）
            datetime.strptime(date_str, "%Y%m%d")
            return date_str
        except ValueError:
            print(f"⚠️ 传入日期{date_str}不合法（无效日期），自动使用今日日期")
            return get_today_date()
    else:
        print(f"⚠️ 传入日期{day}格式错误（需YYYYMMDD），自动使用今日日期")
        return get_today_date()

def parse_remedy_record(remedy_record: Dict[str, Any]) -> Dict[str, Any]:
    """
    解析飞书返回的原始补卡记录，转换为易读的结构化字典
    
    Args:
        remedy_record: 飞书接口返回的单条补卡记录原始数据
        
    Returns:
        Dict[str, Any]: 结构化的补卡记录，包含员工ID、补卡日期、打卡状态等关键信息
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
    
    Args:
        app_id: 飞书应用ID
        app_secret: 飞书应用密钥
        user_id: 员工ID
        
    Raises:
        ValueError: 参数为空或空白字符串时抛出异常
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
    
    特性：
    1. 统一处理请求超时、网络异常
    2. 优先解析响应内容（无论HTTP状态码是否为200）
    3. 捕获JSON解析异常并友好提示
    
    Args:
        method: HTTP请求方法（GET/POST等）
        url: 请求完整URL
        **kwargs: requests.request的其他参数（headers/json/params等）
        
    Returns:
        dict: 飞书接口返回的JSON解析结果
        
    Raises:
        Exception: 网络请求失败/响应非JSON格式时抛出异常
    """
    try:
        response = requests.request(
            method=method,
            url=url,
            timeout=FEISHU_TIMEOUT,** kwargs
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
    
    Args:
        app_id: 飞书应用ID
        app_secret: 飞书应用密钥
        
    Returns:
        str: 有效的tenant_access_token
        
    Raises:
        Exception: Token获取失败/返回空值时抛出异常
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
        # 针对常见错误码补充提示
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
    
    Args:
        user_id: 员工ID
        app_id: 飞书应用ID
        app_secret: 飞书应用密钥
        remedy_date: 补卡日期（YYYYMMDD格式）
        tenant_access_token: 可选，已获取的租户Token（避免重复获取）
        
    Returns:
        Dict[str, Any]: 查询结果字典，包含：
            - message: 结果说明
            - data: 原始补卡数据（成功时有值）
            - result: 解析后的补卡记录字符串
            - code: 业务码
            - log_id: 接口日志ID（用于排查问题）
            
    Raises:
        Exception: 未知业务码/接口调用异常时抛出异常
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
        print(f"✅ 使用传入的租户Token：{final_token[:10]}...（已脱敏）")
    else:
        final_token = get_tenant_access_token(app_id, app_secret)
        print(f"✅ 自动获取新的租户Token：{final_token[:10]}...（已脱敏）")
    
    # 3. 构造补卡查询请求参数
    api_url = f"{FEISHU_BASE_URL}{REMEDY_QUERY_API}"
    headers = {
        "Authorization": f"Bearer {final_token}",
        "Content-Type": "application/json; charset=utf-8"
    }
    params = {"employee_type": "employee_id"}  # 指定user_id类型为员工ID
    payload = {
        "user_id": user_id,
        "remedy_date": remedy_date
    }
    print(f"📤 请求参数：{json.dumps(payload, ensure_ascii=False)}")
    
    # 4. 发送补卡查询请求
    result = feishu_request(
        method="POST",
        url=api_url,
        headers=headers,
        params=params,
        json=payload
    )
    print(f"📥 接口原始返回：{json.dumps(result, ensure_ascii=False, indent=2)}")
    
    # 5. 解析补卡查询响应
    code = result.get("code")
    msg = result.get("msg", "未知信息")
    
    # 处理已知业务码
    if code in FEISHU_ATTENDANCE_CODES:
        # 业务码0：查询成功
        if code == 0:
            raw_data = result.get("data", {})
            allowed_remedys = raw_data.get("user_allowed_remedys", [])
            
            # 无补卡记录
            if not allowed_remedys:
                return {
                    "message": f"该用户{remedy_date}无补卡相关记录",
                    "data": None,
                    "result": "",
                    "code": 0,
                    "log_id": result.get("error", {}).get("log_id", "")
                }
            # 有补卡记录，解析最后一条
            else:
                last_remedy = allowed_remedys[-1]
                parsed_result = parse_remedy_record(last_remedy)
                return {
                    "message": f"成功查询到{remedy_date}补卡记录",
                    "data": last_remedy,
                    "result": str(parsed_result),
                    "code": 0,
                    "log_id": result.get("error", {}).get("log_id", "")
                }
        # 非0业务码：返回对应说明
        else:
            return {
                "message": f"{FEISHU_ATTENDANCE_CODES[code]}（查询日期：{remedy_date}）",
                "data": None,
                "result": "",
                "code": code,
                "log_id": result.get("error", {}).get("log_id", "")
            }
    # 处理未知业务码
    else:
        raise Exception(
            f"补卡查询失败 [业务码:{code}]: {msg} "
            f"| 查询日期:{remedy_date} "
            f"| log_id:{result.get('error', {}).get('log_id', '无')}"
        )

# -------------------------- 核心入口函数 --------------------------
def main(
    user_id: str,
    app_id: str,
    app_secret: str,
    day: int | str | None = None,
    tenant_access_token: str = ""
) -> Dict[str, Any]:
    """
    对外统一调用入口：查询员工补卡权限
    
    Args:
        user_id: 员工ID
        app_id: 飞书应用ID
        app_secret: 飞书应用密钥
        day: 补卡日期（支持数字/字符串/空值，空值自动用今日）
        tenant_access_token: 可选，已获取的租户Token
        
    Returns:
        Dict[str, Any]: 标准化的查询结果字典，包含：
            - message: 结果说明
            - result: 解析后的补卡记录（字符串）
            - data: 原始补卡数据
            - code: 结果码（0成功/-1参数错误/-2其他异常）
            - log_id: 接口日志ID
    """
    try:
        # 解析补卡日期（核心逻辑：无有效日期则用今日）
        remedy_date = parse_remedy_date(day)
        print(f"📅 最终查询日期：{remedy_date}")
        
        # 调用核心查询函数
        return query_feishu_remedy(
            user_id=user_id,
            app_id=app_id,
            app_secret=app_secret,
            remedy_date=remedy_date,
            tenant_access_token=tenant_access_token
        )
    # 参数校验异常
    except ValueError as e:
        return {
            "message": f"参数校验失败：{str(e)}",
            "result": "",
            "data": None,
            "code": -1,
            "log_id": ""
        }
    # 其他异常（网络/接口/解析等）
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
    """
    测试说明：
    1. 替换TEST_CONFIG中的参数为真实值
    2. DAY参数支持：None/空字符串/20260312/20260230（无效日期）等
    3. 运行后会打印请求过程和最终结果
    """
    # 测试配置（请替换为真实参数）
    TEST_CONFIG = {
        "USER_ID": "b5491ce9",
        "APP_ID": "cli_a93bb2cf4d789cc9",
        "APP_SECRET": "aFyVc072SUFfSir3WgwBCd678ShnbwWO",
        "TENANT_TOKEN": "",
        "DAY": 20260312  # 测试用补卡日期
    }

    # 执行补卡查询
    result = main(
        user_id=TEST_CONFIG["USER_ID"],
        app_id=TEST_CONFIG["APP_ID"],
        app_secret=TEST_CONFIG["APP_SECRET"],
        day=TEST_CONFIG["DAY"],
        tenant_access_token=TEST_CONFIG["TENANT_TOKEN"]
    )

    # 打印最终结果
    print("\n📝 最终处理结果：")
    print(json.dumps(result, ensure_ascii=False, indent=2))