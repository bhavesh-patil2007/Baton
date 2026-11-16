from tests.conftest import login


def test_source_connections_lifecycle(client):
    approver = login(client, "approver")
    contributor = login(client, "incoming_contributor")

    # 1. Contributor cannot create connection
    res = client.post(
        "/sources/connections",
        json={"provider": "github", "target_name": "manjirirelekar47/Baton", "acl_label": "L1"},
        headers=contributor,
    )
    assert res.status_code == 403

    # 2. Approver connects GitHub repo
    res = client.post(
        "/sources/connections",
        json={"provider": "github", "target_name": "manjirirelekar47/Baton", "acl_label": "L2"},
        headers=approver,
    )
    assert res.status_code == 201
    conn = res.json()
    assert conn["provider"] == "github"
    assert conn["status"] == "connected"
    conn_id = conn["id"]

    # 3. List connections
    res = client.get("/sources/connections", headers=approver)
    assert res.status_code == 200
    assert any(c["id"] == conn_id for c in res.json())

    # 4. First sync runs and imports records
    res = client.post(f"/sources/connections/{conn_id}/sync", headers=approver)
    assert res.status_code == 200
    sync_res = res.json()
    assert sync_res["status"] == "succeeded"
    assert sync_res["records_added"] > 0
    first_added = sync_res["records_added"]

    # 5. Second sync unchanged: creates no duplicate records, records_skipped increments
    res = client.post(f"/sources/connections/{conn_id}/sync", headers=approver)
    assert res.status_code == 200
    sync_res2 = res.json()
    assert sync_res2["status"] == "succeeded"
    assert sync_res2["records_added"] == first_added
    assert sync_res2["records_skipped"] >= 1

    # 6. Approver connects Jira project and syncs
    jira_res = client.post(
        "/sources/connections",
        json={"provider": "jira", "target_name": "CAMPUS", "acl_label": "L1"},
        headers=approver,
    )
    assert jira_res.status_code == 201
    jira_id = jira_res.json()["id"]

    res = client.post(f"/sources/connections/{jira_id}/sync", headers=approver)
    assert res.status_code == 200
    assert res.json()["status"] == "succeeded"
    assert res.json()["records_added"] > 0

    # 7. Imported sources appear in sources list
    sources = client.get("/sources", headers=approver).json()
    titles = [s["title"] for s in sources]
    assert any("GitHub: manjirirelekar47/Baton" in t for t in titles)
    assert any("Jira: CAMPUS" in t for t in titles)

    # 8. Contributor sees only L1 sources (not L2 GitHub connection source)
    contrib_sources = client.get("/sources", headers=contributor).json()
    contrib_titles = [s["title"] for s in contrib_sources]
    assert any("Jira: CAMPUS" in t for t in contrib_titles)
    assert not any("GitHub: manjirirelekar47/Baton" in t for t in contrib_titles)

    # 9. Delete connection
    res = client.delete(f"/sources/connections/{conn_id}", headers=approver)
    assert res.status_code == 200
    assert res.json()["status"] == "deleted"
