"""Color temperature calculation — pure function of time and config."""


def get_base_temp(hour: int, minute: int, config: dict) -> int:
    """Return the base color temperature in Kelvin for the given time.

    Linear interpolation during dawn/dusk transitions,
    constant DAY_TEMP or NIGHT_TEMP otherwise.
    """
    fractional_hour = hour + minute / 60.0

    dawn_start = config["dawn_start"]
    dawn_end = config["dawn_end"]
    dusk_start = config["dusk_start"]
    dusk_end = config["dusk_end"]
    day_temp = config["day_temp"]
    night_temp = config["night_temp"]

    if dawn_start <= fractional_hour < dawn_end:
        progress = (fractional_hour - dawn_start) / (dawn_end - dawn_start)
        return int(night_temp + (day_temp - night_temp) * progress)
    elif dawn_end <= fractional_hour < dusk_start:
        return day_temp
    elif dusk_start <= fractional_hour < dusk_end:
        progress = (fractional_hour - dusk_start) / (dusk_end - dusk_start)
        return int(day_temp + (night_temp - day_temp) * progress)
    else:
        return night_temp
