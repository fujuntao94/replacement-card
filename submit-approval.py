import requests
import json
import uuid
from datetime import datetime
from typing import Dict, Any
import warnings

# 忽略SSL不安全请求警告
warnings.filterwarnings('ignore', category=requests.packages.urllib3.exceptions.InsecureRequestWarning)

# -------------------------- 全局配置 --------------------------
# 定义Token缓存全局变量（修复问题1）
TOKEN_CACHE = {
    "tenant_token": "",
    "expire_time": 0.0
}

# 自定义参数验证异常类（修复问题3）
class ParameterValidationError(ValueError):
    """参数验证异常类"""
    pass


# 飞书API常量
FEISHU_BASE_URL = "https://open.feishu.cn"
FEISHU_TIMEOUT = 20
TOKEN_API = "/open-apis/auth/v3/tenant_access_token/internal"
REMEDY_SUBMIT_API = "/open-apis/attendance/v1/user_task_remedys"
APPROVAL_CREATE_API = "/open-apis/approval/v4/instances"

# 飞书考勤业务码映射（补卡提交相关）
FEISHU_REMEDY_SUBMIT_CODES = {
    0: "补卡申请提交成功",
    1226601: "补卡时间不在允许范围内",
    1226602: "补卡次数已用完",
    1226603: "用户无补卡权限",
    1226604: "补卡记录不存在",
    1226605: "补卡申请已存在",
    1226501: "无需补卡（用户当日无缺卡记录）"
}

# 打卡类型映射
WORK_TYPE_MAP = {
    1: "上班",
    2: "下班",
    -1: "未知类型"
}

# -------------------------- 通用工具函数 --------------------------
def validate_feishu_params(app_id: str, app_secret: str) -> None:
    """校验飞书基础参数合法性"""
    if not isinstance(app_id, str) or not app_id.strip():
        raise ParameterValidationError("APP_ID不合法：不能为空、不能是空白字符串")
    if not isinstance(app_secret, str) or not app_secret.strip():
        raise ParameterValidationError("APP_SECRET不合法：不能为空、不能是空白字符串")

def validate_remedy_params(remedy_data: Dict[str, Any]) -> None:
    """校验补卡提交参数合法性"""
    required_fields = ["user_id", "remedy_date", "punch_no", "work_type", "normal_punch_time"]
    
    for field in required_fields:
        if field not in remedy_data:
            raise ParameterValidationError(f"补卡参数缺失：{field}")
        
        value = remedy_data[field]
        if field in ["punch_no", "work_type"]:  # 修复：remedy_date是int但格式校验单独做，这里移除避免误判
            if not isinstance(value, int):
                raise ParameterValidationError(f"{field}格式错误，需为整数类型，当前值：{value}（类型：{type(value)}）")
        elif field == "remedy_date":
            if not isinstance(value, (int, str)):
                raise ParameterValidationError(f"{field}格式错误，需为数字或字符串类型，当前值：{value}（类型：{type(value)}）")
        else:
            if not isinstance(value, str) or not value.strip():
                raise ParameterValidationError(f"{field}不能为空或空白字符串，当前值：{value}")
    
    # 校验日期格式
    try:
        str_date = str(remedy_data["remedy_date"])
        datetime.strptime(str_date, "%Y%m%d")
    except ValueError:
        raise ParameterValidationError(f"remedy_date格式错误，需为YYYYMMDD格式，当前值：{remedy_data['remedy_date']}")
    
    # 校验时间格式
    try:
        datetime.strptime(remedy_data["normal_punch_time"], "%Y-%m-%d %H:%M")
    except ValueError:
        raise ParameterValidationError(f"normal_punch_time格式错误，需为YYYY-MM-DD HH:MM格式，当前值：{remedy_data['normal_punch_time']}")

def feishu_request(method: str, url: str, **kwargs) -> dict:
    """通用飞书API请求函数"""
    try:
        response = requests.request(
            method=method,
            url=url,
            timeout=FEISHU_TIMEOUT,** kwargs,
            verify=False  # 关闭SSL验证
        )
        response.encoding = 'utf-8'  # 确保响应编码正确
        try:
            result = response.json()
        except json.JSONDecodeError:
            raise Exception(f"API响应不是JSON格式：{response.text}")
        return result
    except requests.exceptions.RequestException as e:
        raise Exception(f"API请求网络失败：{str(e)}")

def get_tenant_access_token(app_id: str, app_secret: str, force_refresh: bool = False) -> str:
    """统一获取租户Token（带缓存，避免重复请求）"""
    global TOKEN_CACHE
    current_time = datetime.now().timestamp()
    
    # 如果缓存有效且不强制刷新，直接返回缓存
    if not force_refresh and TOKEN_CACHE["tenant_token"] and TOKEN_CACHE["expire_time"] > current_time:
        print(f"使用缓存的Token：{TOKEN_CACHE['tenant_token'][:10]}...")
        return TOKEN_CACHE["tenant_token"]
    
    print("开始获取飞书Tenant Token...")
    
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
            error_msg += "\n可能原因：\n1. APP_ID/APP_SECRET含空格\n2. APP_ID不是cli_开头\n3. 应用未配置为企业自建应用"
        print(error_msg)
        raise Exception(error_msg)
    
    tenant_token = result.get("tenant_access_token")
    expire = result.get("expire", 7200)  # 默认2小时过期
    if not tenant_token:
        raise Exception("Token获取成功，但返回的tenant_access_token为空")
    
    # 更新缓存（提前10分钟过期，避免过期）
    TOKEN_CACHE["tenant_token"] = tenant_token
    TOKEN_CACHE["expire_time"] = current_time + expire - 600
    
    print(f"Token获取成功: {tenant_token[:20]}...")
    return tenant_token

# -------------------------- 补卡接口相关 --------------------------
def submit_feishu_remedy(
    remedy_data: Dict[str, Any],
    app_id: str,
    app_secret: str,
    tenant_access_token: str = "",
    user_id: str = ""  # 修复：设置默认值避免参数缺失
) -> Dict[str, Any]:
    """提交飞书考勤补卡申请"""
    # 优先使用入参user_id，否则从remedy_data中获取
    if not user_id.strip():
        user_id = remedy_data.get("user_id", "")
    
    # 2. 获取Token
    if not tenant_access_token.strip():
        tenant_token = get_tenant_access_token(app_id, app_secret)
    else:
        tenant_token = tenant_access_token.strip()
        print(f"使用传入的Token：{tenant_token[:10]}...")
    
    # 3. 构造补卡请求
    api_url = f"{FEISHU_BASE_URL}{REMEDY_SUBMIT_API}"
    headers = {
        "Authorization": f"Bearer {tenant_token}",
        "Content-Type": "application/json; charset=utf-8"
    }
    submit_payload = {
        "user_id": user_id,
        "remedy_date": remedy_data["remedy_date"],
        "punch_no": remedy_data["punch_no"],
        "work_type": remedy_data["work_type"],
        "remedy_time": remedy_data["normal_punch_time"],
        "reason": "忘记打卡",
        "time": "-"
    }
    params = {"employee_type": "employee_id"}
    
    print(f"提交补卡请求，参数：{json.dumps(submit_payload, ensure_ascii=False)}")
    
    # 4. 发送请求
    result = feishu_request(
        method="POST",
        url=api_url,
        headers=headers,
        params=params,
        json=submit_payload
    )
    
    print(f"补卡接口返回：{json.dumps(result, ensure_ascii=False, indent=2)}")
    
    # 5. 处理响应
    code = result.get("code")
    msg = result.get("msg", "未知信息")
    
    if code in FEISHU_REMEDY_SUBMIT_CODES:
        return {
            "success": code == 0,  # 仅当code=0时为成功
            "message": FEISHU_REMEDY_SUBMIT_CODES[code],
            "data": result.get("data"),
            "result": json.dumps(result, ensure_ascii=False),
            "code": code,
            "log_id": result.get("error", {}).get("log_id", "")
        }
    else:
        return {
            "success": False,
            "message": f"补卡提交结果未知 [业务码:{code}]: {msg}",
            "data": result,
            "result": json.dumps(result, ensure_ascii=False),
            "code": code,
            "log_id": result.get("error", {}).get("log_id", "")
        }

# -------------------------- 审批发起相关 --------------------------
def create_remedy_approval(
    remedy_data: Dict[str, Any],
    app_id: str,
    app_secret: str,
    approval_code: str,
    tenant_token: str = "",
    user_id: str = "",
    approval_id: str = ""
) -> dict:
    """创建自定义补卡审批单（仅补卡成功时调用）"""
    print("========== 开始发起补卡审批 ==========")

    # 1. 获取Token（复用缓存/已有Token）
    if not tenant_token.strip():
        token = get_tenant_access_token(app_id, app_secret)
    else:
        token = tenant_token.strip()
    
    if not token:
        return {"code": -1, "msg": "Token获取失败", "instance_code": "", "success": False}

    if not user_id.strip():
        user_id = remedy_data.get("user_id", "")
    
    # 2. 解析补卡数据为审批表单格式
    work_type_desc = WORK_TYPE_MAP.get(remedy_data["work_type"], "未知类型")
    
    abnormal_date = str(remedy_data["remedy_date"])
    abnormal_date = f"{abnormal_date[:4]}-{abnormal_date[4:6]}-{abnormal_date[6:]}"
    abnormal_record = f"缺卡类型：未打卡，班次类型：{work_type_desc}"
    remedy_time = remedy_data["normal_punch_time"]
    remedy_reason = "忘记打卡"

    # 3. 构造审批表单（替换为你的真实控件ID！！！）
    remedy_form = [
        {"id": "ReplacementCardDate", "type": "input", "value": abnormal_date},    # 异常日期
        {"id": "ReplacementCardRecord", "type": "input", "value": abnormal_record},  # 异常记录
        {"id": "ReplacementCardTime", "type": "input", "value": remedy_time},      # 补卡时间
        {"id": "ReplacementCardReason", "type": "textarea", "value": remedy_reason},  # 补卡事由
        {"id": "ReplacementCardId", "type": "input", "value": approval_id}  # 补卡事由
    ]

    # 4. 发起审批请求
    url = f"{FEISHU_BASE_URL}{APPROVAL_CREATE_API}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }
    request_data = {
        "approval_code": approval_code,
        "user_id": user_id,
        "form": json.dumps(remedy_form, ensure_ascii=False),
        "uuid": str(uuid.uuid4()),
        "with_link": True
    }

    try:
        response = feishu_request(
            method="POST",
            url=url,
            headers=headers,
            json=request_data
        )
        result = response

        if result.get("code") != 0:
            print(f"发起补卡审批失败: {result['msg']}")
            return {
                "code": result["code"], 
                "msg": f"发起失败: {result['msg']}", 
                "instance_code": "",
                "approval_url": "",
                "success": False
            }

        print("补卡审批发起成功！")
        return {
            "code": 0,
            "msg": "发起成功",
            "instance_code": result.get("data", {}).get("instance_code", ""),
            "approval_url": result.get("data", {}).get("approval_url", ""),
            "success": True
        }

    except Exception as e:
        print(f"发起审批异常: {str(e)}", exc_info=True)
        return {
            "code": -3, 
            "msg": f"接口异常: {str(e)}", 
            "instance_code": "",
            "approval_url": "",
            "success": False
        }

# -------------------------- 核心流程入口 --------------------------
def main(
    remedy_data: Dict[str, Any],
    app_id: str,
    app_secret: str,
    approval_code: str = "5E6B37FC-CB66-4B84-8F27-93B3C47D7F15",
    tenant_access_token: str = ""
) -> Dict[str, Any]:
    """
    完整补卡流程：先提交考勤补卡 → 成功则发起审批
    :return: 整合后的流程结果
    """
    # 初始化返回结果
    final_result = {
        "remedy": {},  # 补卡接口结果
        "approval": {},  # 审批发起结果
        "success": 0,
        "message": ""  # 统一返回的提示信息
    }

    # 重置Token缓存（可选，根据业务场景调整）
    global TOKEN_CACHE
    TOKEN_CACHE["tenant_token"] = ""
    TOKEN_CACHE["expire_time"] = 0

    try:
        # 前置操作：先做全量参数校验（修复问题2：调用校验函数）
        print("========== 开始参数校验 ==========")
        # 严格校验所有参数
        validate_feishu_params(app_id, app_secret)
        validate_remedy_params(remedy_data)
        print("参数校验通过，开始执行补卡流程")
        
        user_id = remedy_data['user_id']
        # 第一步：提交补卡申请（修复问题4：传递user_id参数）
        print("========== 开始执行补卡流程 ==========")
        remedy_result = submit_feishu_remedy(
            remedy_data=remedy_data,
            app_id=app_id,
            app_secret=app_secret,
            tenant_access_token=tenant_access_token,
            user_id=user_id
        )
        final_result["remedy"] = remedy_result
        
        # 补卡失败：直接返回失败原因
        if not remedy_result["success"]:
            final_result["message"] = f"补卡失败：{remedy_result['message']}"
            final_result["success"] = 0
            print(final_result["message"])
            return final_result
        
        # 第二步：补卡成功，发起审批（复用已获取的Token）
        print("补卡提交成功，开始发起审批单...")
        # 获取缓存的Token，避免重复请求
        token = TOKEN_CACHE["tenant_token"] if TOKEN_CACHE["tenant_token"] else tenant_access_token
        data = remedy_result.get("data", {})
        user_remedy = data.get("user_remedy", {})
        approval_id = user_remedy.get("approval_id")
        approval_result = create_remedy_approval(
            remedy_data=remedy_data,
            app_id=app_id,
            app_secret=app_secret,
            approval_code=approval_code,
            tenant_token=token,
            user_id=user_id,
            approval_id = approval_id
        )
        final_result["approval"] = approval_result
        
        # 审批成功：返回包含补卡时间、打卡类型的成功提示
        if approval_result["success"]:
            work_type_desc = WORK_TYPE_MAP.get(remedy_data["work_type"], "未知类型")
            remedy_time = remedy_data["normal_punch_time"]
            final_result["message"] = f"{remedy_time} {work_type_desc} 补卡申请已经发起"
            final_result["success"] = 1
            print(final_result["message"])
        
        # 审批失败：返回补卡成功+审批失败的提示
        else:
            final_result["message"] = f"补卡提交成功，但审批发起失败：{approval_result['msg']}"
            final_result["success"] = 0
            print(final_result["message"])
        
        return final_result

    # 捕获参数验证异常（优先级最高，确保绝对终止流程）
    except ParameterValidationError as e:
        error_msg = f"参数验证失败，流程终止：{str(e)}"
        print(error_msg)
        # 强制清空结果，确保没有残留数据
        final_result = {
            "remedy": {},
            "approval": {},
            "success": -1,
            "message": error_msg
        }
        return final_result
        
    # 捕获其他业务异常
    except Exception as e:
        error_msg = f"补卡流程执行异常：{str(e)}"
        print(error_msg, exc_info=True)
        final_result["message"] = error_msg
        final_result["success"] = 0
        final_result["error"] = str(e)
        return final_result

if __name__ == "__main__":
    # 1. 配置信息（替换为你的真实值）
    CONFIG = {
        "APP_ID": "cli_a93bb2cf4d789cc9",
        "APP_SECRET": "aFyVc072SUFfSir3WgwBCd678ShnbwWO",
        "APPROVAL_CODE": "5E6B37FC-CB66-4B84-8F27-93B3C47D7F15",  # 自定义补卡审批模板编码
        "TENANT_TOKEN": ""  # 留空自动获取
    }

    # 2. 补卡数据（测试参数验证失败场景）
    EXTERNAL_REMEDY_DATA = {
        'user_id': 'b5491ce9',  # 空值，会触发参数验证失败
        'remedy_date': 20260323,
        'punch_no': 0,
        'work_type': 1,
        'normal_punch_time': '2026-03-23 09:00',
        'reason': '忘记打卡',
    }

    # 3. 执行完整流程
    flow_result = main(
        remedy_data=EXTERNAL_REMEDY_DATA,
        app_id=CONFIG["APP_ID"],
        app_secret=CONFIG["APP_SECRET"],
        approval_code=CONFIG["APPROVAL_CODE"],
        tenant_access_token=CONFIG["TENANT_TOKEN"]
    )

    # 4. 输出结果（确保中文显示正常）
    print("\n========== 补卡流程最终结果 ==========")
    print(json.dumps(flow_result, ensure_ascii=False, indent=2, sort_keys=False))