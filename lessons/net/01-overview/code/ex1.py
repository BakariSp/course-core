def bottleneck(links):
    """找出这条流的瓶颈链路。

    links 是一个列表，每个元素是 (链路名, 速率) 的二元组，速率单位是 Mbps，
    链路按数据实际经过的顺序排列（从源到目的）。

    返回一个二元组 (瓶颈链路名, 瓶颈链路速率)。
    瓶颈链路是这条流经过的所有链路里速率最小的那一条；
    如果有多条链路的速率并列最小，返回列表中先出现的那一条。
    """
    bottleneck_link = ["",20000]
    for link in links:
          if link[1] < bottleneck_link[1]:
            bottleneck_link = link
    return bottleneck_link

