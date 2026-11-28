"""Phase 6 unit tests: MockMetaPublisherClient timeout/remote-verify, limits, and insights."""

from __future__ import annotations

import pytest

from packages.meta_integrations import (
    AmbiguousPublishTimeoutError,
    MockMetaPublisherClient,
)
from packages.shared.enums import PublishMediaType

pytestmark = pytest.mark.unit


def test_mock_publisher_ambiguous_timeout_and_remote_state_verification() -> None:
    """Acceptance #15: after ambiguous timeout, remote check finds the already-published media."""
    pub = MockMetaPublisherClient(simulate_ambiguous_timeout_once=True)
    container_id = pub.create_container(
        access_token="tok",
        external_account_id="ig_4001",
        media_type=PublishMediaType.REELS,
        media_url="https://storage.local/reel.mp4",
        caption="Launch reel",
        is_ai_generated=True,
    )
    assert pub.get_container_status(access_token="tok", container_id=container_id).status_code == (
        "FINISHED"
    )

    with pytest.raises(AmbiguousPublishTimeoutError):
        pub.publish_container(
            access_token="tok",
            external_account_id="ig_4001",
            container_id=container_id,
        )
    assert pub.publish_call_count == 1

    # Remote-state verification finds the published post without calling publish again!
    verified = pub.find_published_media_for_container(
        access_token="tok",
        external_account_id="ig_4001",
        container_id=container_id,
    )
    assert verified is not None
    assert verified.remote_media_id.startswith("ig_media_")
    assert pub.publish_call_count == 1


def test_mock_publisher_container_expiry_and_insights_fetch() -> None:
    """Expired containers surface EXPIRED status; insights return account + media snapshots."""
    expired_pub = MockMetaPublisherClient(expire_container=True)
    assert (
        expired_pub.get_container_status(access_token="tok", container_id="cnt_1").status_code
        == "EXPIRED"
    )

    insights = expired_pub.fetch_insights(
        access_token="tok",
        external_account_id="ig_4001",
        remote_media_ids=["ig_media_1", "ig_media_2"],
    )
    assert len(insights) == 3
    assert insights[0].scope == "account"
    assert insights[1].remote_media_id == "ig_media_1"
