-- trades_live に order_id (OrderRequest.order_id) と UNIQUE 制約を追加。
--
-- 目的: 二重約定防止 (at-least-once redelivery 対策)。
-- OMS Live は sendorder 成功 + Supabase 書込失敗で Pub/Sub の再配信を踏むと、
-- 同じ ``OrderRequest.order_id`` の注文が再走する。アプリ側の existence check
-- と DB UNIQUE の二段で守る。
--
-- 既存行は order_id NULL のままで非破壊。partial unique index にすることで
-- NULL は複数許容しつつ、order_id ありの行同士のみ重複を弾く。
alter table trades_live add column if not exists order_id uuid;

create unique index if not exists trades_live_order_id_key
    on trades_live (order_id)
    where order_id is not null;
