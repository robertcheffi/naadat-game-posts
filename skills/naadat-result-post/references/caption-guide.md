# Caption guide

English. Written as the team account speaking to its own fans: warm, a little cheeky, proud of the club, never mocking the opponent. Short paragraphs. Only facts present in `game.json` or given by the user.

## Shape

1. **Hook line**: the result in one sentence with the score and opponent. Wins can celebrate; losses stay honest and forward-looking.
2. **Story** (1-3 short sentences, optional for a loss by 4+): how the game went, from the data. Good material: period swings (`periods`), a comeback or a late winner (`goals[].time`, `score_after`), overtime or shootout (`finish`), multi-goal or multi-point games (`scorers_us`), a big goalie night (`goalies[].saves`), a home crowd (`attendance`, when it is a number above 0).
3. **Shout-outs**: name the standout Näädät players with their line. Use what the data holds:
   - full name known and `assists` is a number: full name plus `G+A`, e.g. "Aatu Esimerkki 3+1";
   - only the last name known, or `assists` is `null`: the name as given plus the goal count, e.g. "two goals from Malli". Never write `G+A` when assists are unknown, and never add a first name the data lacks.
4. **Next up** only if the user tells you the next game; the data does not carry it.
5. **Sign-off** and **hashtags**, last, in this order: `caption_signoff` from `assets/brand/team.json`, a blank line, then `hashtags`, then any `competition_hashtags` for the game's `competition` (for FCAA games that puts `#FCAAhockey` last). The sign-off is the team's brand mark: copy it exactly as written in `team.json`, emojis included.

Length: 40-90 words before the sign-off, for every result including a loss by 4+ (keep that one near 40). A game without a goal log (most FCAA games) has less to tell: about 30 words is enough there. The graphic already carries the numbers, so the caption tells the story around them.

To reach the length, use the data first. Beyond that, only lines that state no fact: "Proud of this group.", "On to the next one!", "Thanks for the support!", "Plenty to learn from this one." Atmosphere, crowd mood, injuries, the opponent's form and anything else the data does not hold stay out unless the user said it.

## Tone by result

| result | angle |
|---|---|
| W | confident, credit the group, name the heroes |
| OTW / SOW | drama, nerves, "extra time", the hero of OT/SO |
| OTL / SOL | close one, fight shown, came within a goal |
| L by 1-3 | close game, what went right, on to the next |
| L by 4+ | short and gracious: credit effort, a positive (goalie saves, a first goal of the season), look ahead. No excuses, no blaming referees |

## Facts to get right

- `home` / `away` are the teams as listed by the results service (or entered by hand); `us_side` says which one is Näädät. The score is written Näädät-first in the caption ("Näädät took a 7-6 win over ...", "... fell 3-9 to ...").
- Scorer names come from `scorers_us[].name`: `full` when it is filled, otherwise `last`. Never add goals, assists or saves that are not in the data.
- When `scorers_us` adds up to fewer goals than Näädät scored, the list is partial: name the known scorers ("goals from Esimerkki and Malli") without implying they scored all of them.
- Standings and points ("two points", "top of the table") only when the user states them; `game.json` carries neither.
- `type` on a goal: PP power play, SH short-handed, EN empty net.
- Times in `goals[].time` are elapsed game time (`43:10` is 3:10 into the third period).
- Tag an opponent account only when the user gives its handle.

## Examples

The teams, players and scores below are invented to show the shape, and every fact in them is the kind `game.json` or the user would supply (the first-goal-of-the-season remark comes from the user). Write each real caption from its own `game.json`.

Win, III-divisioona, full data:
> What a third period! Näädät beat the Riverside Ravens 6-3 after pulling away with three straight goals in the final frame.
>
> Aatu Esimerkki led the way with a hat trick (3+1), Onni Malli added 1+3 and Eero Kuvitteellinen stood tall with 31 saves.
>
> Proud of this group. On to the next one!
>
> 💚🧡
>
> #naadathockey #hernesaarennäädät #näädät #jääkiekko #icehockey #aaltouniversity

Win, FCAA, hand-entered with last names and goals only:
> Näädät take a 5-2 win over the Harbour Hawks in FCAA play!
>
> Esimerkki and Malli scored two goals each, and Kuvitteellinen added one more. Three different scorers and a three-goal margin at the final horn.
>
> Proud of this group. On to the next one!
>
> 💚🧡
>
> #naadathockey #hernesaarennäädät #näädät #jääkiekko #icehockey #aaltouniversity #FCAAhockey

Loss by 4+, the user mentioned it was Esimerkki's first goal of the season:
> A tough night for Näädät, who fell 2-9 to the Northside Wolves.
>
> Eero Kuvitteellinen made 39 saves, and Aatu Esimerkki scored his first goal of the season. Plenty to learn from this one, and the group sticks together. Thanks for the support!
>
> 💚🧡
>
> #naadathockey #hernesaarennäädät #näädät #jääkiekko #icehockey #aaltouniversity
