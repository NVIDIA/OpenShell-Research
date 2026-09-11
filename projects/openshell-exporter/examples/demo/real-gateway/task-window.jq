def event_epoch:
  ((if ((.occurred_at // "") == "") then .received_at else .occurred_at end)
    | sub("\\.[0-9]+Z$"; "Z")
    | fromdateiso8601);

($start | fromdateiso8601) as $started
| ($finish | fromdateiso8601) as $finished
| [.[] | select((event_epoch >= ($started - 5)) and (event_epoch <= ($finished + 30)))]
