def external_member_alpha(client, payload):
    first = client.fetch(payload, 10)
    second = client.authorize(first, 20)
    third = client.persist(second, 30)
    fourth = client.publish(third, 40)
    fifth = client.confirm(fourth, 50)
    sixth = client.audit(fifth, 60)
    seventh = client.finish(sixth, 70)
    return seventh
