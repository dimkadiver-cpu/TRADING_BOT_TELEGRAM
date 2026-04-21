Perche quando salva raw_message non vengono popolate:
- source_chat_title

    if peer is basic group or channel/supergroup:
    source_chat_title = peer.title
elif peer is user:
    source_chat_title = full_name_or_none(peer)
else:
    source_chat_title = None


- source_type

    Non usarlo per distinguere “topic” o “discussion thread”.
    Usalo solo come tipo del peer reale:

    - channel
    - supergroup
    - group
    - user

def resolve_source_type(chat) -> str | None:
    if getattr(chat, "broadcast", False):
        return "channel"
    if getattr(chat, "megagroup", False):
        return "supergroup"
    if getattr(chat, "username", None) is not None:
        return "user"
    return "group"

- source_trader_a //  puotrebbe essere risolto da channel config e topit / ho in casi di multi trade con layer perse minimale
