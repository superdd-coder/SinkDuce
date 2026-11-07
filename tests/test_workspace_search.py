"""Keyword search must respect the same resource boundaries as opening content."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.identity import Actor, set_actor, reset_actor
from src.identity import acl
from src.search import workspace as search
from src.search.routes import router
from src.meeting import store as meetings
from src.notes import store as notes


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / 'collections'
    root.mkdir()
    monkeypatch.setattr(search.collections, 'COLLECTIONS_DIR', root)
    metadata, meeting_list, note_list, sections, note_text = [], [], [], {}, {}
    monkeypatch.setattr(search.collections, 'list_collections_meta', lambda: metadata)
    monkeypatch.setattr(meetings, 'list_meetings', lambda: meeting_list)
    monkeypatch.setattr(meetings, 'get_section_md', lambda mid, sid: sections.get((mid, sid)))
    monkeypatch.setattr(notes, 'list_notes', lambda: note_list)
    monkeypatch.setattr(notes, 'get_content', lambda nid: note_text[nid])
    token = set_actor(Actor())
    search._cache.clear()
    search._cache_bytes = 0
    def project(cid, name, archived=False):
        metadata.append(dict(id=cid, name=name, archived=archived))
        path = root / cid
        path.mkdir()
        conn = sqlite3.connect(path / 'meta.db')
        conn.executescript('''
            CREATE TABLE folders(folder_id TEXT PRIMARY KEY, parent_folder_id TEXT, name TEXT, archived INTEGER DEFAULT 0);
            CREATE TABLE files(file_id TEXT PRIMARY KEY, current_version_id TEXT, archived INTEGER DEFAULT 0, source TEXT, source_label TEXT);
            CREATE TABLE file_versions(version_id TEXT PRIMARY KEY, file_id TEXT, storage_file_id TEXT, archived INTEGER DEFAULT 0);
            CREATE TABLE file_paths(path_id TEXT PRIMARY KEY, file_id TEXT, folder_id TEXT, is_primary INTEGER DEFAULT 0, archived INTEGER DEFAULT 0);
        ''')
        conn.close()
        return path
    def file(cid, fid, name, text='', version='v1', archived=False, folder=None):
        path = root / cid
        with sqlite3.connect(path / 'meta.db') as db:
            db.execute('INSERT INTO files VALUES(?,?,?,?,?)', (fid,version,int(archived),None,name))
            db.execute('INSERT INTO file_versions VALUES(?,?,?,0)', (version,fid,name))
            db.execute('INSERT INTO file_paths VALUES(?,?,?,1,0)', ('path-'+fid,fid,folder))
        artifact = path / 'files' / fid / version / 'parsed.txt'
        artifact.parent.mkdir(parents=True)
        artifact.write_text(text)
        return artifact
    def meeting(mid, title, summary='', tabs=None, archived=False, allocated=None):
        meeting_list.append(SimpleNamespace(id=mid, title=title, summary=summary, tabs=tabs or [],
            created_at=datetime(2026,11,6,tzinfo=timezone.utc), archived=archived, allocated_collections=allocated or []))
    yield SimpleNamespace(root=root,metadata=metadata, project=project, file=file, meeting=meeting,
        meetings=meeting_list, notes=note_list, sections=sections, note_text=note_text)
    reset_actor(token)


def group(q, category='files', **kw):
    return search.search_workspace(q, category, **kw)['groups'][0]


def test_groups_keywords_snippets_and_targets(workspace):
    w=workspace
    w.project('p', '研究项目')
    w.file('p','f','方案.pdf','项目的预算：中文 MixedCase 与 [abc] 100%_ 字面关键词。')
    w.meeting('m','项目周会','项目的总体纪要', [{'tab_id':'tab_a','title':'预算安排'}])
    w.sections['m','tab_a']='**项目**的成本分配'
    w.notes.append(SimpleNamespace(id='n',collection='p',title='项目笔记'))
    w.note_text['n']='项目的讨论记录'
    response=search.search_workspace('项目')
    assert {g['category']:g['total'] for g in response['groups']} == dict(projects=1,files=1,meetings=1,minutes=2,notes=1)
    assert group('中文 mixedCASE [abc] 100%_')['total']==1
    assert group('中文 missing')['total']==0
    assert group('预算', 'minutes')['items'][0]['section_id']=='tab_a'
    assert group('成本','minutes')['items'][0]['snippet']=='项目的成本分配'
    assert group('讨论','notes')['items'][0]['collection_id']=='p'
    assert 'path' not in group('中文')['items'][0]


def test_pagination_hundreds_of_files_stable_and_unique(workspace):
    w=workspace; w.project('p','资料')
    for i in range(205): w.file('p',f'f{i}',f'Report {i:03d}.txt',version=f'v{i}')
    first=group('report',limit=8)
    assert first['total']==205 and first['has_more'] and first['next_offset']==8
    pages=[group('report',limit=50,offset=i) for i in range(0,250,50)]
    ids=[hit['id'] for page in pages for hit in page['items']]
    assert len(ids)==len(set(ids))==205
    assert not pages[-1]['has_more']
    assert group('report',offset=999)['items']==[]


def test_current_version_and_folder_hierarchy_archive(workspace):
    w=workspace; path=w.project('p','计划'); w.project('old','已归档',True)
    with sqlite3.connect(path/'meta.db') as db:
        db.executemany('INSERT INTO folders VALUES(?,?,?,?)',[('a',None,'一级',0),('b','a','二级',0),('gone',None,'已归档',1),('child','gone','子层',0)])
    w.file('p','f','report.pdf','CurrentOnly', folder='b')
    w.file('p','hidden','report.pdf','CurrentOnly',folder='child',version='v2')
    w.file('p','deleted','report.pdf','CurrentOnly',archived=True,version='v3')
    w.file('old','f','report.pdf','CurrentOnly')
    old=path/'files'/'f'/'v0'; old.mkdir(); (old/'parsed.txt').write_text('OldOnly')
    assert group('CurrentOnly')['total']==1
    assert group('report')['items'][0]['context']=='计划 / 一级 / 二级'
    assert group('OldOnly')['total']==0
    # A live second mount keeps a file visible even if its primary mount is archived.
    with sqlite3.connect(path/'meta.db') as db:
        db.execute("INSERT INTO file_paths VALUES('other','hidden','b',0,0)")
    assert group('CurrentOnly')['total']==2
    w.meeting('archived','report',archived=True)
    w.meeting('allocated','report',allocated=['old'])
    assert group('report','meetings')['total']==0


def test_acl_before_content_read_and_cache_does_not_bypass_it(workspace,monkeypatch):
    w=workspace; w.project('public','keyword'); w.project('private','keyword')
    w.file('public','f','keyword.txt','keyword'); w.file('private','s','keyword.txt','secret keyword')
    assert group('keyword')['total']==2  # populate text cache as owner
    token=set_actor(Actor(id='member',kind='key',role_id='member'))
    monkeypatch.setattr(acl,'collection_allows',lambda cid,tier,role:cid=='public')
    monkeypatch.setattr(acl,'domain_allows',lambda *args:False)
    original=search._file_documents
    def checked(meta):
        assert meta['id']=='public'  # forbidden resources never even reach IO
        return original(meta)
    monkeypatch.setattr(search,'_file_documents',checked)
    monkeypatch.setattr(meetings,'list_meetings',lambda:pytest.fail('Denied meeting domain was read'))
    try:
        response=search.search_workspace('keyword')
        assert {g['category']:g['total'] for g in response['groups']}==dict(projects=1,files=1,meetings=0,minutes=0,notes=0)
        monkeypatch.setattr(acl,'collection_allows',lambda *args:False)
        assert group('keyword')['total']==0
    finally: reset_actor(token)


def test_module_disabled_prevents_library_reads(workspace,monkeypatch):
    w=workspace; w.project('p','keyword')
    token=set_actor(Actor(id='restricted',kind='key',role_id='custom'))
    monkeypatch.setattr(search,'get_role',lambda _:SimpleNamespace(allows_module=lambda _:False))
    monkeypatch.setattr(search,'can',lambda *_:False)
    monkeypatch.setattr(search,'_file_documents',lambda _:pytest.fail('Disabled module was searched'))
    try: assert all(g['total']==0 for g in search.search_workspace('keyword')['groups'])
    finally: reset_actor(token)


def test_update_delete_and_large_content_are_searchable(workspace):
    w=workspace; w.project('p','资料')
    path=w.file('p','f','file.pdf','old keyword')
    assert group('old')['total']==1
    path.write_text('new keyword with changed size')
    assert group('new')['total']==1 and group('old')['total']==0
    path.write_text('x'*(2*1024*1024+65534)+'boundaryterm'+'x'*70000+' laterterm')
    assert group('boundaryterm laterterm')['total']==1
    path.unlink()
    assert group('laterterm')['total']==0
    assert group('file.pdf')['total']==1  # unparsed documents are still found by name


def test_legacy_no_database_and_safe_paths(workspace):
    w=workspace; path=w.project('legacy','旧库'); (path/'meta.db').unlink()
    (path/'files.json').write_text(json.dumps({'f':{'source_label':'legacy.pdf'},'n':{'source':'__note__:n','source_label':'legacy note'}}))
    assert group('legacy')['total']==1
    assert not (path/'meta.db').exists()  # no migration on a read
    with pytest.raises(ValueError): search._safe_child(path,'../../outside')


def test_one_broken_collection_keeps_other_categories(workspace):
    w=workspace; path=w.project('broken','keyword'); (path/'meta.db').write_text('not sqlite')
    w.project('ok','keyword'); w.file('ok','f','keyword.pdf')
    result=search.search_workspace('keyword')
    assert result['unavailable_categories']==['files']
    assert next(g for g in result['groups'] if g['category']=='files')['total']==1
    assert next(g for g in result['groups'] if g['category']=='projects')['total']==2


def test_empty_query_does_no_io(workspace,monkeypatch):
    monkeypatch.setattr(search.collections,'list_collections_meta',lambda:pytest.fail('Empty query scanned disk'))
    assert all(not g['items'] for g in search.search_workspace('  ')['groups'])


def test_endpoint_validation_and_thread_actor_context(workspace,monkeypatch):
    app=FastAPI(); app.include_router(router,prefix='/api')
    @app.middleware('http')
    async def actor_context(request, call_next):
        token=set_actor(Actor(id='restricted',kind='key',role_id='custom'))
        try: return await call_next(request)
        finally: reset_actor(token)
    def role(rid):
        assert rid=='custom'  # sync worker preserved request actor
        return SimpleNamespace(allows_module=lambda _:False)
    monkeypatch.setattr(search,'get_role',role)
    monkeypatch.setattr(search,'can',lambda *_:False)
    with TestClient(app) as client:
        assert client.get('/api/search/workspace',params={'q':'anything'}).status_code==200
        for params in [{'q':'a'*201},{'limit':51},{'offset':-1},{'category':'secret'}]:
            assert client.get('/api/search/workspace',params=params).status_code==422


def test_readonly_snapshot_includes_uncheckpointed_wal(workspace):
    w=workspace; path=w.project('p','资料'); w.file('p','f','before.pdf')
    db=sqlite3.connect(path/'meta.db')
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute("UPDATE file_versions SET storage_file_id='after.pdf'")
        db.commit()  # keep writer open so the change remains in WAL
        assert (path/'meta.db-wal').stat().st_size>0
        _,_,rows=search._read_index_copy(path/'meta.db')
        assert rows[0]['storage_file_id']=='after.pdf'
    finally: db.close()


def test_meeting_name_only_does_not_load_minutes(workspace,monkeypatch):
    workspace.meeting('m','weekly meeting')
    monkeypatch.setattr(meetings,'get_section_md',lambda *_:pytest.fail('Name filter read full minutes'))
    assert group('weekly','meetings')['total']==1
    assert search.plain('成本 [spk:1] 分配 [stt_001,stt_002]')=='成本 分配'
