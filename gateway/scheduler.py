class SchedulerPlaceholder:
    """Cron placeholder for daily inspection and weekly report jobs."""
    enabled: bool = False

    def register_jobs(self) -> None:
        """Register scheduled jobs in a future iteration."""
        return None
