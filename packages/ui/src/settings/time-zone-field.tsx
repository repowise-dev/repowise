"use client";

/**
 * Time zone picker: "Auto" (this browser's zone) or any zone the runtime
 * knows, searchable by name. Storage is the host's job; this only reports the
 * choice, "auto" or an IANA name.
 */

import { useMemo } from "react";
import { ModelCombobox, type ModelChoice, type ModelComboboxProps } from "./model-combobox";

export const AUTO_TIME_ZONE_CHOICE = "auto";

export interface TimeZoneFieldLabels {
  /** The Auto row's note, given the detected zone. */
  auto: (detected: string) => string;
  /** The closed field when Auto is chosen, given the detected zone. */
  autoValue: (detected: string) => string;
  placeholder: string;
  noMatch: string;
}

const DEFAULT_LABELS: TimeZoneFieldLabels = {
  auto: (detected) => `This browser, ${detected}`,
  autoValue: (detected) => `Auto (${detected})`,
  placeholder: "Search time zones",
  noMatch: "No time zone matches.",
};

export interface TimeZoneFieldProps
  extends Pick<ModelComboboxProps, "id" | "ariaLabel" | "className"> {
  /** "auto" or an IANA zone name. */
  value: string;
  /** This browser's zone, shown beside Auto. */
  detected: string;
  onChange: (value: string) => void;
  labels?: Partial<TimeZoneFieldLabels>;
}

/** Every zone `Intl` lists, with UTC first; some engines leave UTC out. */
export function timeZoneChoices(): string[] {
  let zones: string[] = [];
  try {
    zones = Intl.supportedValuesOf("timeZone");
  } catch {
    // An engine without the API still offers Auto and UTC.
  }
  return ["UTC", ...zones.filter((z) => z !== "UTC")];
}

export function TimeZoneField({
  value,
  detected,
  onChange,
  labels: overrides,
  ...field
}: TimeZoneFieldProps) {
  const labels = { ...DEFAULT_LABELS, ...overrides };
  const autoNote = labels.auto(detected);
  const choices: ModelChoice[] = useMemo(
    () => [
      { id: AUTO_TIME_ZONE_CHOICE, label: autoNote },
      ...timeZoneChoices().map((zone) => ({ id: zone })),
    ],
    [autoNote],
  );
  return (
    <ModelCombobox
      {...field}
      value={value}
      displayValue={value === AUTO_TIME_ZONE_CHOICE ? labels.autoValue(detected) : undefined}
      models={choices}
      onSelect={onChange}
      allowTyped={false}
      labels={{ placeholder: labels.placeholder, noMatch: labels.noMatch }}
    />
  );
}
