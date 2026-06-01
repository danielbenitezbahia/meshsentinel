export type Faction = "player" | "ai1" | "ai2" | "ai3";

export interface GameCell {
  h3Index:      string;
  owner:        Faction;
  troops:       number;
  nodeId?:      string;
  nodeName:     string;
  isNodeActive: boolean;
  isSynthetic:  boolean;
  isProduction: boolean;
}

export interface Bridge {
  fromH3: string;
  toH3:   string;
}
