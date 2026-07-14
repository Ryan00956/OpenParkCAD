from openparkcad.candidate_selector import _stall_delta
from openparkcad.models import CandidateObject


def test_connector_stall_delta_counts_removed_stalls():
    connector = CandidateObject(
        id="connector",
        kind="aisle_skeleton",
        role="connector",
        status="rejected",
        score_features={"added_stalls": 3.0, "removed_stalls": 5.0},
    )

    assert _stall_delta(connector) == -2.0

