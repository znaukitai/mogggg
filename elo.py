import config


def elo_step(score_a: float, score_b: float) -> int:
    """How many elo points the battle is worth, by the gap between the two scores.
    Gap up to 1.0 inclusive -> 25, above 1.0 -> 28."""
    gap = round(abs(score_a - score_b), 1)  # scores are shown with 1 decimal; avoid float noise
    return config.ELO_STEP_BIG if gap > config.BIG_GAP else config.ELO_STEP
