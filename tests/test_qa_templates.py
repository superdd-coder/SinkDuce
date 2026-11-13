"""Project templates and frozen workbook exports."""

from io import BytesIO

import openpyxl

from tests.test_qa import create, mutate, qa_fixture  # noqa: F401


def test_template_applies_metadata_and_remains_frozen_in_prior_export(qa):
    client, base = qa
    r = client.post(
        base + "/templates",
        json={
            "name": "Standard exchange",
            "title": "Garden review",
            "metadata_position": "right",
            "include_number": False,
            "include_status": True,
            "include_type": True,
        },
    )
    assert r.status_code == 201, r.text
    template = r.json()
    assert "include_type" not in template
    d = create(qa)
    d = mutate(qa, d, "/questions", {"text": "Provide the orchard route map."})
    d = mutate(qa, d, "", {"template_id": template["id"]}, "patch")
    r = client.post(f"{base}/lists/{d['id']}/exports", json={"expected_revision": d["revision"]})
    assert r.status_code == 200, r.text
    eid = r.json()["id"]
    url = f"{base}/lists/{d['id']}/exports/{eid}/download"
    first = client.get(url).content
    ws = openpyxl.load_workbook(BytesIO(first)).active
    assert ws["A1"].value == "Garden review"
    assert [c.value for c in ws[4]] == ["Question", "Status", "A response", "B response"]
    r = client.patch(
        base + f"/templates/{template['id']}",
        json={"expected_revision": 1, "name": "Updated exchange", "title": "New review"},
    )
    assert r.status_code == 200, r.text
    assert client.get(url).content == first
    assert client.get(f"{base}/lists/{d['id']}/exports").json()[0]["id"] == eid


def test_template_logo_is_embedded_and_internal_ids_are_not_written(qa):
    from zipfile import ZipFile

    from PIL import Image

    client, base = qa
    image = BytesIO()
    Image.new("RGB", (24, 12), "#336644").save(image, format="PNG")
    r = client.post(base + "/logos", files={"file": ("example-logo.png", image.getvalue())})
    assert r.status_code == 200, r.text
    logo = r.json()["id"]
    template = client.post(
        base + "/templates",
        json={"name": "Logo template", "logo_id": logo, "logo_position": "left"},
    ).json()
    d = create(qa)
    d = mutate(qa, d, "", {"template_id": template["id"]}, "patch")
    record = client.post(
        f"{base}/lists/{d['id']}/exports", json={"expected_revision": d["revision"]}
    ).json()
    blob = client.get(f"{base}/lists/{d['id']}/exports/{record['id']}/download").content
    with ZipFile(BytesIO(blob)) as archive:
        assert any(name.startswith("xl/media/") for name in archive.namelist())
        worksheet = archive.read("xl/worksheets/sheet1.xml").decode()
        assert logo not in worksheet and template["id"] not in worksheet
    ws = openpyxl.load_workbook(BytesIO(blob)).active
    assert ws["B1"].value == d["title"]
