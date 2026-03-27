import requests
import json
import uuid
from datetime import datetime
from typing import Dict, Any, List
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
        if field in ["punch_no", "work_type"]:
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
    expire = result.get("expire", 7200)
    if not tenant_token:
        raise Exception("Token获取成功，但返回的tenant_access_token为空")
    
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
    user_id: str = ""
) -> Dict[str, Any]:
    """提交飞书考勤补卡申请"""
    if not user_id.strip():
        user_id = remedy_data.get("user_id", "")
    
    if not tenant_access_token.strip():
        tenant_token = get_tenant_access_token(app_id, app_secret)
    else:
        tenant_token = tenant_access_token.strip()
        print(f"使用传入的Token：{tenant_token[:10]}...")
    
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
    
    result = feishu_request(
        method="POST",
        url=api_url,
        headers=headers,
        params=params,
        json=submit_payload
    )
    
    print(f"补卡接口返回：{json.dumps(result, ensure_ascii=False, indent=2)}")
    
    code = result.get("code")
    msg = result.get("msg", "未知信息")
    
    if code in FEISHU_REMEDY_SUBMIT_CODES:
        return {
            "success": code == 0,
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
    """创建自定义补卡审批单"""
    print("========== 开始发起补卡审批 ==========")

    if not tenant_token.strip():
        token = get_tenant_access_token(app_id, app_secret)
    else:
        token = tenant_token.strip()
    
    if not token:
        return {"code": -1, "msg": "Token获取失败", "instance_code": "", "success": False}

    if not user_id.strip():
        user_id = remedy_data.get("user_id", "")
    
    work_type_desc = WORK_TYPE_MAP.get(remedy_data["work_type"], "未知类型")
    
    abnormal_date = str(remedy_data["remedy_date"])
    abnormal_date = f"{abnormal_date[:4]}-{abnormal_date[4:6]}-{abnormal_date[6:]}"
    abnormal_record = f"缺卡类型：未打卡，班次类型：{work_type_desc}"
    remedy_time = remedy_data["normal_punch_time"]
    remedy_reason = "忘记打卡"

    remedy_form = [
        {"id": "ReplacementCardDate", "type": "input", "value": abnormal_date},
        {"id": "ReplacementCardRecord", "type": "input", "value": abnormal_record},
        {"id": "ReplacementCardTime", "type": "input", "value": remedy_time},
        {"id": "ReplacementCardReason", "type": "textarea", "value": remedy_reason},
        {"id": "ReplacementCardId", "type": "input", "value": approval_id}
    ]

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
        response = feishu_request("POST", url, headers=headers, json=request_data)
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

# -------------------------- 核心流程入口（已修改：支持数组，默认取第一条） --------------------------
def main(
    remedy_data: List[Dict[str, Any]] | Dict[str, Any],  # 支持数组 / 对象
    app_id: str,
    app_secret: str,
    approval_code: str = "5E6B37FC-CB66-4B84-8F27-93B3C47D7F15",
    tenant_access_token: str = ""
) -> Dict[str, Any]:
    """
    完整补卡流程：支持传入数组，默认取第一条进行提交
    :return: 整合后的流程结果
    """
    final_result = {
        "remedy": {},
        "approval": {},
        "success": 0,
        "message": ""
    }

    global TOKEN_CACHE
    TOKEN_CACHE["tenant_token"] = ""
    TOKEN_CACHE["expire_time"] = 0

    try:
        # ====================== 核心修改：数组自动取第一条 ======================
        if isinstance(remedy_data, list):
            if len(remedy_data) == 0:
                raise ParameterValidationError("补卡数据数组为空，无法提交")
            print(f"📦 检测到传入数组，自动取第1条数据进行补卡提交")
            remedy_data = remedy_data[0]
        # ====================================================================

        print("========== 开始参数校验 ==========")
        validate_feishu_params(app_id, app_secret)
        validate_remedy_params(remedy_data)
        print("参数校验通过，开始执行补卡流程")
        
        user_id = remedy_data['user_id']

        print("========== 开始执行补卡流程 ==========")
        remedy_result = submit_feishu_remedy(
            remedy_data=remedy_data,
            app_id=app_id,
            app_secret=app_secret,
            tenant_access_token=tenant_access_token,
            user_id=user_id
        )
        final_result["remedy"] = remedy_result
        
        if not remedy_result["success"]:
            final_result["message"] = f"补卡失败：{remedy_result['message']}"
            final_result["success"] = 0
            return final_result
        
        print("补卡提交成功，开始发起审批单...")
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
            approval_id=approval_id
        )
        final_result["approval"] = approval_result
        
        if approval_result["success"]:
            work_type_desc = WORK_TYPE_MAP.get(remedy_data["work_type"], "未知类型")
            remedy_time = remedy_data["normal_punch_time"]
            final_result["message"] = f"{remedy_time} {work_type_desc} 补卡申请已经发起"
            final_result["success"] = 1
        else:
            final_result["message"] = f"补卡提交成功，但审批发起失败：{approval_result['msg']}"
            final_result["success"] = 0
        
        return final_result

    except ParameterValidationError as e:
        error_msg = f"参数验证失败，流程终止：{str(e)}"
        print(error_msg)
        final_result = {
            "remedy": {},
            "approval": {},
            "success": -1,
            "message": error_msg
        }
        return final_result
        
    except Exception as e:
        error_msg = f"补卡流程执行异常：{str(e)}"
        print(error_msg, exc_info=True)
        final_result["message"] = error_msg
        final_result["success"] = 0
        final_result["error"] = str(e)
        return final_result

if __name__ == "__main__":
    CONFIG = {
        "APP_ID": "cli_a93bb2cf4d789cc9",
        "APP_SECRET": "aFyVc072SUFfSir3WgwBCd678ShnbwWO",
        "APPROVAL_CODE": "5E6B37FC-CB66-4B84-8F27-93B3C47D7F15",
        "TENANT_TOKEN": ""
    }

    # ========== 测试：传入数组 ==========
    EXTERNAL_REMEDY_DATA = [
        {
            'user_id': 'b5491ce9',
            'remedy_date': 20260323,
            'punch_no': 0,
            'work_type': 1,
            'normal_punch_time': '2026-03-23 09:00',
            'reason': '忘记打卡',
        }
    ]

    flow_result = main(
        remedy_data=EXTERNAL_REMEDY_DATA,
        app_id=CONFIG["APP_ID"],
        app_secret=CONFIG["APP_SECRET"],
        approval_code=CONFIG["APPROVAL_CODE"],
        tenant_access_token=CONFIG["TENANT_TOKEN"]
    )

    print("\n========== 补卡流程最终结果 ==========")
    print(json.dumps(flow_result, ensure_ascii=False, indent=2, sort_keys=False))