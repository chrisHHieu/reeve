// Whether a block is drawn in the expanded view (the ⤢ button): the same block, given the room
// to be explored, so a chart is taller and a map takes the wheel without Ctrl.

import { createContext, useContext } from "react";

export const VisualExpanded = createContext(false);

export const useExpanded = () => useContext(VisualExpanded);
