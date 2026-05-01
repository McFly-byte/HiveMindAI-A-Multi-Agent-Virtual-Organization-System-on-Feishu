import pytest
from adaptors.feishu.errors import BaseFieldValidationError
from adaptors.feishu.field_mapper import FieldMapper


def test_field_mapper_allows_configured_field() -> None:
    FieldMapper().validate_fields("Tasks", {"任务名称": "接口开发"})


def test_field_mapper_rejects_unknown_field() -> None:
    with pytest.raises(BaseFieldValidationError):
        FieldMapper().validate_fields("Tasks", {"未配置字段": "x"})
