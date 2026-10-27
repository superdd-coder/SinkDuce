import io

from PIL import Image


def png():
    out = io.BytesIO()
    Image.new('RGB', (300, 100), '#1a5e3d').save(out, format='PNG')
    return out.getvalue()


def test_source_images_stay_in_the_cited_file_and_version(tmp_path, monkeypatch):
    from src.file_mgmt import storage_paths
    from src.reports.assets import SourceImages
    monkeypatch.setattr(storage_paths, 'COLLECTIONS_DIR', tmp_path)
    root = tmp_path / 'col' / 'files' / 'file-a'
    for version in ('v1', 'v2'):
        d = root / version / 'images'
        d.mkdir(parents=True)
        (d / 'image-a.png').write_bytes(png())
    e = {'source': {'collection': 'col', 'file_id': 'file-a', 'version_id': 'v1'}}
    resolver = SourceImages('session', tmp_path / 'attachments')
    fields = {'image_id': 'image-a', 'file_id': 'file-a'}
    assert resolver(e, fields) == png()
    assert resolver({"source": {"collection": "col", "file_id": "file-a"}}, fields) == png(), "Older citations still resolve managed image IDs"
    assert resolver(e, {**fields, 'file_id': 'other-file'}) is None
    (root / 'v1' / 'images' / 'image-a.png').unlink()
    assert resolver(e, fields) is None, 'Do not substitute an image from another version'
    assert resolver(e, {**fields, 'image_id': '../../outside'}) is None


def test_source_images_check_collection_access_before_reading(tmp_path, monkeypatch):
    from src.identity import visibility
    from src.reports import assets
    monkeypatch.setattr(assets, 'get_actor', lambda: object())
    def denied(*args):
        from fastapi import HTTPException
        raise HTTPException(404)
    monkeypatch.setattr(visibility, 'assert_can_read', denied)
    resolver = assets.SourceImages('session', tmp_path)
    assert resolver({'source': {'collection': 'col', 'file_id': 'file'}}, {'file_id': 'file', 'image_id': 'image'}) is None


def test_attachment_source_images_are_scoped_to_the_report_session(tmp_path):
    import base64
    from src.chat_attachments.store import AttachmentStore
    from src.reports.assets import SourceImages
    store = AttachmentStore(tmp_path)
    a = store.create('session-a', 'image.png', b'input')
    store.finish('session-a', a.id, content='', position_map=[], parser='test', images=[{'id': 'image-a', 'url': 'data:image/png;base64,' + base64.b64encode(png()).decode()}])
    evidence = {'source': {'attachment_id': a.id}, 'location': {'image_index': 0}}
    assert SourceImages('session-a', tmp_path)(evidence, {}) == png()
    assert SourceImages('session-b', tmp_path)(evidence, {}) is None
