from app.checks import security
from app.checks.base import Severity


def test_never_calls_secrets_api(fake_ctx):
    """Core invariant: this module must never read Secret objects, even via a
    generic-looking method name. Guards against a future edit accidentally
    reintroducing a secrets call."""
    security.run(fake_ctx)

    for mock_attr_name in dir(fake_ctx.core):
        if "secret" in mock_attr_name.lower():
            method = getattr(fake_ctx.core, mock_attr_name)
            if hasattr(method, "assert_not_called"):
                method.assert_not_called()


def test_secrets_excluded_finding_always_present(fake_ctx):
    result = security.run(fake_ctx)
    assert any(f.check_id == "secrets_excluded" for f in result.findings)


def test_individual_user_cluster_admin_binding_is_critical(fake_ctx, make_ns):
    crb = make_ns(
        metadata=make_ns(name="globaladmin-user-9mtlz"),
        role_ref=make_ns(name="cluster-admin"),
        subjects=[make_ns(kind="User", name="user-9mtlz")],
    )
    fake_ctx.rbac.list_cluster_role_binding.return_value = make_ns(items=[crb])

    result = security.run(fake_ctx)

    matches = [f for f in result.findings if f.check_id == "cluster_admin_binding"]
    assert len(matches) == 1
    assert matches[0].severity == Severity.CRITICAL
    assert "user-9mtlz" in matches[0].title


def test_system_masters_group_binding_is_not_flagged(fake_ctx, make_ns):
    crb = make_ns(
        metadata=make_ns(name="cluster-admin"),
        role_ref=make_ns(name="cluster-admin"),
        subjects=[make_ns(kind="Group", name="system:masters")],
    )
    fake_ctx.rbac.list_cluster_role_binding.return_value = make_ns(items=[crb])

    result = security.run(fake_ctx)

    assert not [f for f in result.findings if f.check_id == "cluster_admin_binding"]
