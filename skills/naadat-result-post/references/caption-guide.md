# Caption guide

English. Written as the team account speaking to its own fans: warm, a little cheeky, proud of the club, never mocking the opponent. Short paragraphs. Only facts present in `game.json` or given by the user.

## Shape

1. **Hook line**: the result in one sentence with the score and opponent. Wins can celebrate; losses stay honest and forward-looking.
2. **Story** (1-3 short sentences, optional for a blowout loss): how the game went, from the data. Good material: period swings (`periods`), a comeback or a late winner (`goals[].time`, `score_after`), overtime or shootout (`finish`), multi-goal or multi-point games (`scorers_us`), a big goalie night (`goalies[].saves`), a home crowd (`attendance`).
3. **Shout-outs**: name the standout Näädät players by full name with their line, e.g. "Leevi Kuosmanen 3+3". Goals+assists format is `G+A`.
4. **Next up** only if the user tells you the next game; the script does not know it reliably for the caption.
5. **Sign-off** and **hashtags**: `caption_signoff` then a blank line, then `hashtags` from `assets/brand/team.json`, in that order, plus `#FCAAhockey` for FCAA games.

Length: 40-90 words before the hashtags. The graphic already carries the numbers, so the caption tells the story around them.

## Tone by result

| result | angle |
|---|---|
| W | confident, credit the group, name the heroes |
| OTW / SOW | drama, nerves, "extra time", the hero of OT/SO |
| OTL / SOL | point earned, fight shown, close one |
| L by 1-3 | close game, what went right, on to the next |
| L by 4+ | short and gracious: credit effort, a positive (goalie saves, a first goal of the season), look ahead. No excuses, no blaming referees |

## Facts to get right

- `home` / `away` are the teams as listed by the results service; `us_side` says which one is Näädät. The score is written Näädät-first in the caption ("Näädät took a 7-6 win over ...", "... fell 4-28 to KHK").
- Scorer names come from `scorers_us[].name.full`. Never add goals, assists or saves that are not in the data.
- `type` on a goal: PP power play, SH short-handed, EN empty net.
- Times in `goals[].time` are elapsed game time (`43:10` is 3:10 into the third period).
- Tag an opponent account only when the user gives its handle.

## Examples

Win:
> Two points stay home! Näädät beat EVU Team 11-9 at Kaarela after a wild third period.
>
> Leevi Kuosmanen led the way with a hat trick (3+3), Tuukka Lahtinen added 2+4 and Rasmus Inkovaara stood tall with 37 saves.
>
> 💚🧡
>
> #naadathockey #hernesaarennäädät ...

Heavy loss:
> Tough night out in Oulunkylä as Näädät fell 4-28 to KHK.
>
> Goals from Purhonen, Kataja, Posti and Koskimäki, and a lot to build on. Back at it next week.
>
> 💚🧡
