-- market_regime: 当日の地合い判定と Gateway guard 観測用の履歴。
create table if not exists market_regime (
    valid_date date primary key,
    regime text not null check (regime in ('NORMAL', 'CAUTION', 'RISK_OFF', 'CRASH')),
    confidence numeric not null check (confidence >= 0 and confidence <= 1),
    buy_enabled boolean not null,
    position_size_multiplier numeric not null check (position_size_multiplier >= 0),
    metrics jsonb not null default '{}'::jsonb,
    rationale jsonb not null default '[]'::jsonb,
    source text not null default 'universe_scanner',
    created_at timestamptz not null default now()
);

create index if not exists market_regime_created_at_idx
    on market_regime (created_at desc);
