"use client";

import { useEffect, useState } from "react";

import type { Menu, MenuGroup, StaffApi } from "@/lib/api";

export function SoldOut({
  api,
  run,
}: {
  api: StaffApi;
  run: (request: () => Promise<unknown>) => Promise<void>;
}) {
  const [menu, setMenu] = useState<Menu | null>(null);
  const [failed, setFailed] = useState(false);
  // Bumped to load the menu again after a toggle.
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let current = true;
    api.menu().then(
      (loaded) => {
        if (!current) return;
        setMenu(loaded);
        setFailed(false);
      },
      () => {
        if (current) setFailed(true);
      },
    );
    return () => {
      current = false;
    };
  }, [api, version]);

  const toggle = async (request: () => Promise<unknown>) => {
    await run(request);
    setVersion((count) => count + 1);
  };

  if (menu === null) {
    return <p className="empty">{failed ? "Could not load the menu." : "Loading menu…"}</p>;
  }

  return (
    <main className="menu">
      {menu.categories.map((category) => (
        <section key={category.id}>
          <h2>{category.name}</h2>
          {category.items.map((item) => (
            <Toggle
              key={item.id}
              name={item.name}
              available={item.is_available}
              onChange={(available) =>
                toggle(() => api.setItemAvailable(item.id, available))
              }
            />
          ))}
        </section>
      ))}
      {optionGroups(menu).map((group) => (
        <section key={group.id}>
          <h2>Options: {group.name}</h2>
          {group.options.map((option) => (
            <Toggle
              key={option.id}
              name={option.name}
              available={option.is_available}
              onChange={(available) =>
                toggle(() => api.setOptionAvailable(option.id, available))
              }
            />
          ))}
        </section>
      ))}
    </main>
  );
}

/** Each modifier group once, though several items may share it. */
function optionGroups(menu: Menu): MenuGroup[] {
  const groups = new Map<string, MenuGroup>();
  for (const category of menu.categories) {
    for (const item of category.items) {
      for (const group of item.modifier_groups) groups.set(group.id, group);
    }
  }
  return [...groups.values()];
}

function Toggle({
  name,
  available,
  onChange,
}: {
  name: string;
  available: boolean;
  onChange: (available: boolean) => void;
}) {
  return (
    <button
      className={available ? "toggle" : "toggle off"}
      aria-pressed={!available}
      onClick={() => onChange(!available)}
    >
      <span>{name}</span>
      <span className="state">{available ? "Available" : "Sold out"}</span>
    </button>
  );
}
