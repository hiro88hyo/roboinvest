-- positions.scheduled_exit_date: swing max-hold exit date fixed at entry time.
alter table positions
    add column if not exists scheduled_exit_date date;
