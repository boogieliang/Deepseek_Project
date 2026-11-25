"""Field catalogue and value helpers for the form filler.

The catalogue is intentionally data-driven: aliases live here, while matching
logic lives in :mod:`formfiller.matcher`.  Custom user fields are merged into
the same representation at runtime.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class FieldDef:
    key: str
    label: str
    group: str
    type: str = "text"
    aliases: tuple[str, ...] = ()
    negatives: tuple[str, ...] = ()
    autocomplete: tuple[str, ...] = ()
    hint: str = ""
    custom: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "group": self.group,
            "type": self.type,
            "aliases": list(self.aliases),
            "negatives": list(self.negatives),
            "autocomplete": list(self.autocomplete),
            "hint": self.hint,
            "custom": self.custom,
        }


def _f(
    key: str,
    label: str,
    group: str,
    aliases: Iterable[str],
    *,
    type: str = "text",
    negatives: Iterable[str] = (),
    autocomplete: Iterable[str] = (),
    hint: str = "",
) -> FieldDef:
    return FieldDef(
        key=key,
        label=label,
        group=group,
        type=type,
        aliases=tuple(aliases),
        negatives=tuple(negatives),
        autocomplete=tuple(autocomplete),
        hint=hint,
    )


FIELD_DEFS: tuple[FieldDef, ...] = (
    # ------------------------------------------------------------------ 基本信息
    _f(
        "name", "姓名", "基本信息",
        ("姓名", "名字", "真实姓名", "您的姓名", "申请人姓名", "联系人", "联系人姓名",
         "收件人", "收件人姓名", "中文姓名", "客户姓名", "用户姓名", "full name",
         "fullname", "name", "your name", "real name", "contact name", "applicant name"),
        negatives=("公司名称", "单位名称", "企业名称", "用户名", "昵称", "英文名", "英文姓名",
                   "法定代表人", "学校名称", "文件名称", "商品名称"),
        autocomplete=("name",),
        hint="网页上要求填写姓名的输入框",
    ),
    _f(
        "last_name", "姓", "基本信息",
        ("姓氏", "姓", "英文姓", "last name", "lastname", "surname", "family name"),
        negatives=("姓名", "名字", "公司"),
        autocomplete=("family-name",),
    ),
    _f(
        "first_name", "名", "基本信息",
        ("名字", "名", "英文名", "first name", "firstname", "given name"),
        negatives=("姓名", "姓氏", "公司名称"),
        autocomplete=("given-name",),
    ),
    _f(
        "english_name", "英文名", "基本信息",
        ("英文名", "英文姓名", "英文名字", "english name", "name in english"),
        negatives=("中文姓名",),
    ),
    _f(
        "gender", "性别", "基本信息",
        ("性别", "性别选择", "您的性别", "gender", "sex"),
        type="choice",
        autocomplete=("sex",),
    ),
    _f(
        "birthday", "出生日期", "基本信息",
        ("出生日期", "生日", "出生年月", "出生年月日", "出生时间", "date of birth",
         "birthday", "birth date", "dob", "bday"),
        type="date",
        negatives=("入学日期", "毕业日期", "到岗日期", "有效期", "失效日期"),
        autocomplete=("bday",),
    ),
    _f(
        "id_number", "身份证号", "基本信息",
        ("身份证号", "身份证号码", "身份证", "证件号", "证件号码", "身份证件号",
         "公民身份号码", "id number", "identity number", "id card", "national id"),
        negatives=("学号", "工号", "订单号", "手机号", "银行卡号", "护照号"),
        hint="身份证或证件号码",
    ),
    _f(
        "nationality", "国籍", "基本信息",
        ("国籍", "国家/地区", "国家", "nationality", "country", "country/region"),
        type="choice",
        autocomplete=("country",),
    ),
    _f(
        "ethnicity", "民族", "基本信息",
        ("民族", "ethnicity", "ethnic group"),
        type="choice",
    ),
    _f(
        "marital_status", "婚姻状况", "基本信息",
        ("婚姻状况", "婚姻状态", "marital status"),
        type="choice",
    ),
    # ------------------------------------------------------------------ 联系方式
    _f(
        "phone", "手机号", "联系方式",
        ("手机号", "手机号码", "手机", "移动电话", "联系电话", "联系方式", "电话",
         "电话号码", "您的电话", "手机号/电话", "mobile phone", "mobile", "phone",
         "phone number", "cell phone", "telephone number", "contact phone"),
        type="tel",
        negatives=("验证码", "短信验证码", "区号", "分机", "固定电话", "座机", "办公电话",
                   "紧急联系人电话", "身份证号", "订单号"),
        autocomplete=("tel", "tel-national", "tel-local"),
        hint="手机或联系电话",
    ),
    _f(
        "telephone", "固定电话", "联系方式",
        ("固定电话", "座机", "办公电话", "家庭电话", "单位电话", "电话号码", "电话",
         "landline", "telephone", "office phone", "home phone"),
        type="tel",
        negatives=("手机", "移动电话", "验证码", "分机"),
    ),
    _f(
        "email", "电子邮箱", "联系方式",
        ("邮箱", "电子邮箱", "电子邮件", "邮箱地址", "邮件地址", "电子信箱", "联系邮箱",
         "确认邮箱", "再次输入邮箱", "email", "e-mail", "mail", "email address",
         "e-mail address", "confirm email"),
        type="email",
        negatives=("验证码", "邮箱验证码", "邮件验证码"),
        autocomplete=("email",),
        hint="常用于注册、报名、订阅表单",
    ),
    _f(
        "wechat", "微信号", "联系方式",
        ("微信", "微信号", "微信账号", "微信 ID", "wechat", "weixin"),
        negatives=("微信公众号", "微信支付"),
    ),
    _f(
        "qq", "QQ号", "联系方式",
        ("QQ", "QQ号", "QQ号码", "腾讯QQ", "qq number"),
        negatives=("QQ邮箱",),
    ),
    # ------------------------------------------------------------------ 地址信息
    _f(
        "province", "省份", "地址信息",
        ("省份", "所在省", "省/直辖市", "省/自治区", "省份/地区", "province", "state",
         "region"),
        type="choice",
        autocomplete=("address-level1",),
    ),
    _f(
        "city", "城市", "地址信息",
        ("城市", "所在市", "城市/地区", "所在城市", "市", "city", "town"),
        type="choice",
        autocomplete=("address-level2",),
    ),
    _f(
        "district", "区/县", "地址信息",
        ("区县", "所在区", "区/县", "县", "区", "街道/乡镇", "district", "county",
         "address level 3"),
        type="choice",
        autocomplete=("address-level3",),
    ),
    _f(
        "address", "详细地址", "地址信息",
        ("详细地址", "地址", "通讯地址", "联系地址", "收货地址", "家庭地址", "现居地址",
         "居住地址", "街道地址", "地址详情", "常驻地址", "邮寄地址", "address",
         "street address", "address line", "address line 1", "mailing address",
         "shipping address", "home address", "full address"),
        negatives=("邮箱地址", "邮件地址", "IP 地址", "ip地址", "网址", "公司地址", "单位地址"),
        autocomplete=("street-address", "address-line1", "address-line2"),
        hint="街道、门牌号等详细地址",
    ),
    _f(
        "postal_code", "邮政编码", "地址信息",
        ("邮编", "邮政编码", "邮政编号", "邮政编码/邮编", "postal code", "postcode",
         "zip", "zip code"),
        type="number",
        autocomplete=("postal-code",),
    ),
    # ------------------------------------------------------------------ 工作与教育
    _f(
        "company", "公司名称", "工作与教育",
        ("公司", "公司名称", "单位", "单位名称", "企业名称", "工作单位", "所在公司",
         "雇主", "任职单位", "company", "company name", "organization", "employer",
         "business name"),
        negatives=("公司地址", "公司电话", "公司邮箱", "用户名"),
        autocomplete=("organization",),
    ),
    _f(
        "department", "部门", "工作与教育",
        ("部门", "所在部门", "科室", "院系", "系别", "department", "dept"),
        negatives=("部门人数",),
    ),
    _f(
        "position", "职位", "工作与教育",
        ("职位", "岗位", "职务", "头衔", "职称", "担任职务", "position", "job title",
         "title", "role", "occupation"),
        negatives=("标题", "文章标题", "页面标题"),
        autocomplete=("organization-title",),
    ),
    _f(
        "industry", "所属行业", "工作与教育",
        ("行业", "所属行业", "所在行业", "industry", "sector"),
        type="choice",
    ),
    _f(
        "school", "毕业院校", "工作与教育",
        ("学校", "学校名称", "毕业院校", "院校", "母校", "就读学校", "school",
         "university", "college", "school name", "alma mater"),
        negatives=("学校地址", "学校电话"),
    ),
    _f(
        "major", "专业", "工作与教育",
        ("专业", "所学专业", "专业名称", "就读专业", "major", "field of study",
         "specialty"),
    ),
    _f(
        "degree", "学历/学位", "工作与教育",
        ("学历", "学位", "最高学历", "文化程度", "教育程度", "degree",
         "education level", "highest education", "qualification"),
        type="choice",
    ),
    _f(
        "graduation_year", "毕业年份", "工作与教育",
        ("毕业年份", "毕业时间", "毕业年度", "graduation year", "year of graduation"),
        type="number",
        negatives=("入学年份",),
    ),
    _f(
        "work_years", "工作年限", "工作与教育",
        ("工作年限", "工作经验", "从业年限", "工作经历", "years of experience",
         "work experience", "experience"),
        type="number",
        negatives=("项目经验",),
    ),
    # ------------------------------------------------------------------ 账号与其他
    _f(
        "username", "用户名", "账号与其他",
        ("用户名", "账号", "登录名", "用户账号", "登录账号", "username", "user name",
         "login", "login name", "account", "account name"),
        negatives=("银行账号", "银行卡号", "账户名", "收款账号", "对公账号"),
        autocomplete=("username",),
    ),
    _f(
        "password", "密码", "账号与其他",
        ("密码", "登录密码", "账户密码", "用户密码", "password", "passcode", "pass word"),
        type="password",
        negatives=("验证码", "支付密码", "交易密码", "确认密码", "再次输入密码"),
        autocomplete=("current-password", "new-password"),
        hint="仅在你明确需要时填写",
    ),
    _f(
        "website", "个人网站", "账号与其他",
        ("网站", "个人网站", "个人主页", "主页", "网址", "website", "homepage", "url",
         "web site", "portfolio"),
        type="url",
        negatives=("公司网站", "网站名称"),
        autocomplete=("url",),
    ),
    _f(
        "remarks", "备注", "账号与其他",
        ("备注", "说明", "补充说明", "留言", "其他说明", "备注信息", "remarks", "notes",
         "message", "comments", "additional information"),
        type="textarea",
        negatives=("验证码", "短信"),
    ),
    _f(
        "agree", "同意条款", "账号与其他",
        ("同意", "我已阅读并同意", "我同意", "协议", "条款", "隐私政策", "服务协议",
         "agree", "terms", "privacy", "consent", "accept"),
        type="checkbox",
        hint="勾选同意类复选框",
    ),
)

FIELD_BY_KEY: Dict[str, FieldDef] = {f.key: f for f in FIELD_DEFS}

GROUP_ORDER: tuple[str, ...] = (
    "基本信息", "联系方式", "地址信息", "工作与教育", "账号与其他",
)

# Values that mean "yes" when auto-checking a consent checkbox.
TRUTHY_VALUES = {"1", "true", "yes", "y", "on", "是", "同意", "已同意", "勾选"}

# Autocomplete tokens that unambiguously identify a field.
AUTOCOMPLETE_MAP: Dict[str, str] = {
    "name": "name",
    "given-name": "first_name",
    "additional-name": "first_name",
    "family-name": "last_name",
    "email": "email",
    "tel": "phone",
    "tel-national": "phone",
    "tel-local": "phone",
    "street-address": "address",
    "address-line1": "address",
    "address-line2": "address",
    "address-level1": "province",
    "address-level2": "city",
    "address-level3": "district",
    "postal-code": "postal_code",
    "organization": "company",
    "organization-title": "position",
    "bday": "birthday",
    "sex": "gender",
    "username": "username",
    "current-password": "password",
    "new-password": "password",
    "url": "website",
    "country": "nationality",
}


def normalize(text: Any) -> str:
    """Normalize text for matching, preserving CJK characters."""
    if text is None:
        return ""
    s = unicodedata.normalize("NFKC", str(text)).lower()
    s = s.replace("请输入", "").replace("请填写", "").replace("请选择", "")
    s = re.sub(r"[\s\*＊:：\-_/\\|（）()\[\]【】<>《》.,，。;；'\"“”‘’]+", "", s)
    return s.strip()


def human_key(value: Any) -> str:
    """Coerce a profile value to a clean single-line string."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (list, tuple)):
        return "、".join(str(v) for v in value if v is not None)
    return str(value).strip()


def make_custom_def(key: str, label: str) -> FieldDef:
    label = (label or "自定义字段").strip()
    return FieldDef(
        key=key,
        label=label,
        group="自定义",
        type="text",
        aliases=(label,),
        custom=True,
    )


def all_field_defs(custom_fields: Optional[List[Dict[str, Any]]] = None) -> List[FieldDef]:
    defs = list(FIELD_DEFS)
    for item in custom_fields or []:
        key = str(item.get("key") or "").strip()
        label = str(item.get("label") or "").strip()
        if key and label and key not in FIELD_BY_KEY:
            defs.append(make_custom_def(key, label))
    return defs


def candidate_values(key: str, value: str) -> List[str]:
    """Return acceptable spellings for option/radio matching."""
    value = (value or "").strip()
    if not value:
        return []
    out = [value]
    if key == "gender":
        table = {
            "男": ["男", "男性", "male", "m", "先生"],
            "女": ["女", "女性", "female", "f", "女士"],
            "其他": ["其他", "保密", "other", "non-binary"],
            "保密": ["保密", "其他", "other", "prefer not to say"],
        }
        out = table.get(value, out)
    elif key == "degree":
        table = {
            "高中": ["高中", "高中及以下"],
            "大专": ["大专", "专科", "大学专科"],
            "本科": ["本科", "大学本科", "学士"],
            "硕士": ["硕士", "研究生", "硕士研究生"],
            "博士": ["博士", "博士研究生"],
        }
        out = table.get(value, out)
    elif key in ("province", "city", "district"):
        # "广东" should match "广东省"; JS also strips these suffixes.
        stripped = re.sub(r"(省|市|区|县|自治区|特别行政区)$", "", value)
        if stripped and stripped != value:
            out.append(stripped)
    return list(dict.fromkeys(out))
