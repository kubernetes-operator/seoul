from app.checks import health
from app.checks.base import Severity


def test_node_not_ready_is_critical(fake_ctx, make_ns):
    node = make_ns(
        metadata=make_ns(name="playcekubewrk01"),
        status=make_ns(conditions=[make_ns(type="Ready", status="False")]),
    )
    fake_ctx.core.list_node.return_value = make_ns(items=[node])

    result = health.run(fake_ctx)

    matches = [f for f in result.findings if f.check_id == "node_not_ready"]
    assert len(matches) == 1
    assert matches[0].severity == Severity.CRITICAL
    assert matches[0].resource == "playcekubewrk01"


def test_healthy_node_produces_no_node_findings(fake_ctx, make_ns):
    node = make_ns(
        metadata=make_ns(name="playcekubewrk01"),
        status=make_ns(conditions=[
            make_ns(type="Ready", status="True"),
            make_ns(type="MemoryPressure", status="False"),
        ]),
    )
    fake_ctx.core.list_node.return_value = make_ns(items=[node])

    result = health.run(fake_ctx)

    assert not [f for f in result.findings if f.check_id in ("node_not_ready", "node_pressure")]


def test_repeated_warning_events_flagged_but_not_isolated_ones(fake_ctx, make_ns):
    def event(reason, name):
        return make_ns(
            type="Warning", reason=reason, message="boom",
            metadata=make_ns(namespace="default"),
            involved_object=make_ns(name=name),
        )

    fake_ctx.core.list_event_for_all_namespaces.return_value = make_ns(items=[
        event("VolumeFailedDelete", "pv-1"),
        event("VolumeFailedDelete", "pv-1"),
        event("VolumeFailedDelete", "pv-1"),
        event("OneOff", "pod-x"),
    ])

    result = health.run(fake_ctx)

    reasons = {f.check_id for f in result.findings if f.check_id == "repeated_warning_event"}
    assert "repeated_warning_event" in reasons
    assert len([f for f in result.findings if "OneOff" in f.title]) == 0


def test_metrics_server_unavailable_degrades_not_crashes(fake_ctx, make_ns):
    fake_ctx.custom.list_cluster_custom_object.side_effect = Exception("no metrics-server")

    result = health.run(fake_ctx)

    assert result.status == "degraded"
    assert any("metrics-server" in reason for reason in result.status_reasons)
