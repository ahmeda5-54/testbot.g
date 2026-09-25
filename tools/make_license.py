"""Legacy entry point: activation codes are configured manually now."""
if __name__ == "__main__":
    raise SystemExit(
        "Signed codes are no longer supported. Set TRIAL_ACTIVATION_CODE and "
        "PERMANENT_ACTIVATION_CODE in .env or hosting variables, then restart. "
        "See ACTIVATION_CODES.md."
    )
