from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, EmailStr, field_validator, model_validator


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


class Signup(Input):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str
    password_confirm: str

    @field_validator('name')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('이름을 입력하세요.')
        return value.strip()


class Login(Input):
    email: EmailStr
    password: str


class PasswordChange(Input):
    email: EmailStr
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
