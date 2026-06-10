-- Run this once in the Supabase SQL editor to create the three tables.

create table if not exists telemetry (
  id         bigserial primary key,
  ts         timestamptz not null default now(),
  ch1_t      float,
  ch1_h      float,
  ch3_t      float,
  ch3_h      float,
  heater     boolean,
  solenoid   boolean,
  solenoid2  boolean,
  setpoint   float
);

create table if not exists cycle_runs (
  id            bigserial primary key,
  started_at    timestamptz not null default now(),
  ended_at      timestamptz,
  charge_sp     float,
  charge_dur_s  integer,
  cool_to       float,
  delta_t       float,
  num_cycles    integer,
  start_phase   text,
  outcome       text   -- 'done' | 'stopped'
);

create table if not exists cycle_events (
  id         bigserial primary key,
  run_id     bigint references cycle_runs(id) on delete cascade,
  ts         timestamptz not null default now(),
  phase      text,      -- 'charging' | 'cooling' | 'discharging'
  cycle_num  integer,
  elapsed_s  integer
);

-- Optional: index for time-range queries on telemetry
create index if not exists telemetry_ts_idx on telemetry (ts desc);
