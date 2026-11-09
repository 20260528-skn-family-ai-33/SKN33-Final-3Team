from datetime import date
from typing import Annotated, Literal
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.networks import validate_email


def case_sensitive_email(value: str) -> str:
    value = value.strip()
    # EmailStr normalizes domain casing. Validate the address but retain the
    # spelling used as the account identifier (including domain casing).
    validate_email(value)
    if '<' in value or '>' in value:
        raise ValueError('이름 없이 이메일 주소만 입력하세요.')
    return value


AccountEmail = Annotated[str, AfterValidator(case_sensitive_email)]


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class HsItem(Input):
    hs_code: str = Field(pattern=r'^(?:\d{4}|\d{6})$')
    hs_ver: Literal['HS2022']


class Period(Input):
    start: date
    end: date

    @model_validator(mode='after')
    def ordered(self):
        if self.start > self.end:
            raise ValueError('시작일은 종료일보다 늦을 수 없습니다.')
        return self


class Conditions(Input):
    hs_items: list[HsItem] = Field(default_factory=list, max_length=100)
    country_codes: list[str] = Field(default_factory=list, max_length=250)
    period: Period
    compare_period: Period | None = None
    trade_flow: Literal['EXPORT', 'IMPORT'] = 'EXPORT'
    compare: Literal['NONE', 'MOM', 'YOY', 'YOY_PERIOD', 'CUSTOM'] = 'YOY_PERIOD'
    metric: Literal['AMOUNT', 'GROWTH', 'SHARE', 'RANK'] = 'AMOUNT'


class ReportConditions(Conditions):
    include_korea_total: bool = False


class Signup(Input):
    name: str = Field(min_length=1, max_length=120)
    email: AccountEmail
    password: str
    password_confirm: str

    @field_validator('name')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('이름을 입력하세요.')
        return value.strip()


class Login(Input):
    email: AccountEmail
    password: str


class PasswordChange(Input):
    email: AccountEmail
    current_password: str
    new_password: str
    new_password_confirm: str


class InterestUpdate(Input):
    hs_items: list[HsItem]
    country_codes: list[str]
    expected_version: str = Field(pattern=r'^[0-9a-f]{64}$')
    landing_page: Literal['GLOBAL', 'INTERESTED'] | None = None


class NotificationPatch(Input):
    notifications_enabled: bool | None = None
    spike_popup: bool | None = None
    major_issue_popup: bool | None = None
    report_popup: bool | None = None

    @model_validator(mode='before')
    @classmethod
    def reject_null(cls, values):
        if any(v is None for v in values.values()):
            raise ValueError('null은 허용되지 않습니다. 유지할 필드는 생략하세요.')
        return values


class ReportCreate(Input):
    preview_id: str
    confirm_conditions: Literal[True]
    sections: list[Literal['KPI','TOP_COUNTRIES','GROWTH_RISK','LOCAL_ISSUES']] = Field(default_factory=lambda: ['KPI','TOP_COUNTRIES','GROWTH_RISK','LOCAL_ISSUES'])
    formats: list[Literal['PDF','DOCX']] = Field(default_factory=lambda: ['PDF'], min_length=1)
    session_id: str | None = None


class AdminChange(Input):
    expected_version: str | None = None
    reason: str = Field(min_length=1, max_length=1000)
    confirmed: Literal[True]

    @field_validator('reason')
    @classmethod
    def reason_not_blank(cls, value):
        if not value.strip():
            raise ValueError('변경 사유가 필요합니다.')
        return value.strip()
