def recent_average(scores: list[float], window: int = 3) -> float:
    """返回最近 window 次分数的平均值。

    - scores 按时间从早到晚排列，每个分数在 0 到 1 之间。
    - 次数不足 window 次时，有几次就算几次。
    - scores 为空时返回 0.0。

    例：recent_average([0, 1, 1, 1]) == 1.0（只看最后 3 次）
    """
    """
    var: scores length, window (for last window times), repeat time = index
    steps:
        count total iteration time
        in iteration:
            count which score in scores to add
            total score += current_score
        average_score = total_score / total_iteration_time
        return total_iteration_time
    """
   
    #count scores length
    scores_len = len(scores)
    total_score = 0
    #count total iteration time
    total_iteration_time = window - 1 if window - 1 > scores_len else scores_len
    #add each score
    if total_iteration_time > 0:
        for i in range(total_iteration_time):
            total_score += scores[i]
    else:
        total_score = 0
    average_score = total_score / total_iteration_time
    return average_score
        