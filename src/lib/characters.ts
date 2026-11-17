const CHARACTERS: Record<string, string> = {
  wilson: "威尔逊",
  willow: "薇洛",
  wolfgang: "沃尔夫冈",
  wendy: "温蒂",
  wx78: "WX-78",
  wickerbottom: "薇克巴顿",
  woodie: "伍迪",
  wes: "韦斯",
  maxwell: "麦斯威尔",
  waxwell: "麦斯威尔",
  wigfrid: "薇格弗德",
  wathgrithr: "薇格弗德",
  webber: "韦伯",
  walani: "瓦拉尼",
  warly: "沃利",
  woodlegs: "伍德莱格",
  wilba: "威尔巴",
  wormwood: "沃姆伍德",
  winona: "薇诺娜",
  wortox: "沃拓克斯",
  wurt: "沃特",
  walter: "沃尔特",
  wanda: "旺达",
  wonkey: "猴王",
};

export function characterLabel(prefab: string): string {
  if (!prefab) return "未知角色";
  return CHARACTERS[prefab] ?? prefab;
}
