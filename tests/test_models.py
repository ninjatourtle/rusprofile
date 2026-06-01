from rusprofile_parser.models import CompanyResult


def test_company_result_to_dict() -> None:
    result = CompanyResult(
        name="ООО Тест",
        url="https://www.rusprofile.ru/id/1",
        inn="7700000000",
        ogrn="1027700000000",
        registration_date="01.01.2020",
        director_appointment_date="01.01.2020",
        revenue=10,
        profit=1,
        email=None,
        website=None,
        matched=True,
        reasons=["ok"],
    )
    assert result.to_dict()["matched"] is True
    assert result.to_dict()["name"] == "ООО Тест"
