-- trades_paper: unified_signal_id の部分 unique index (重複 Pub/Sub 配信対策)
-- unified_signal_id が NULL でない行 (通常シグナル) に限定。
-- closeout / swing 自動決済由来 (NULL) は対象外。
create unique index if not exists trades_paper_unified_signal_id_key
    on trades_paper (unified_signal_id)
    where unified_signal_id is not null;
