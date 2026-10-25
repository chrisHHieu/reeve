// The schedule the form can say: a choice of days and a time of day. It is written to the
// server as a five-field cron and read back from one. A cron the form cannot say (one the
// agent wrote: every 15 minutes, the first of the month) reads back as null, and the form
// shows it as written and leaves it alone unless the person sets a new one.

export const DAYS: Record<string, { label: string; dow: string }> = {
  daily: { label: "Every day", dow: "*" },
  weekdays: { label: "Weekdays", dow: "1-5" },
  weekends: { label: "Weekends", dow: "0,6" },
  mon: { label: "Mondays", dow: "1" },
  tue: { label: "Tuesdays", dow: "2" },
  wed: { label: "Wednesdays", dow: "3" },
  thu: { label: "Thursdays", dow: "4" },
  fri: { label: "Fridays", dow: "5" },
  sat: { label: "Saturdays", dow: "6" },
  sun: { label: "Sundays", dow: "0" },
};

export const DAY_OPTIONS = Object.entries(DAYS).map(([value, day]) => ({ value, label: day.label }));

const whole = (text: string) => (/^\d+$/.test(text) ? Number(text) : NaN);

export function toCron(day: string, time: string): string {
  const [hour, minute] = (time || "").split(":").map(whole);
  const h = hour >= 0 && hour <= 23 ? hour : 9;
  const m = minute >= 0 && minute <= 59 ? minute : 0;
  return `${m} ${h} * * ${DAYS[day]?.dow ?? "*"}`;
}

export function fromCron(cron: string | null | undefined): { day: string; time: string } | null {
  const fields = (cron || "").trim().split(/\s+/);
  if (fields.length !== 5) return null;
  const [minute, hour, dayOfMonth, month, dow] = fields;
  const m = whole(minute);
  const h = whole(hour);
  if (!(m >= 0 && m <= 59) || !(h >= 0 && h <= 23) || dayOfMonth !== "*" || month !== "*") return null;
  // Sunday is written 0 or 7, and the weekend either way round.
  const spelled = dow === "7" ? "0" : dow === "6,0" ? "0,6" : dow;
  const day = Object.keys(DAYS).find((key) => DAYS[key].dow === spelled);
  if (!day) return null;
  return { day, time: `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}` };
}
