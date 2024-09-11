#!/usr/bin/env python3

import xml.etree.ElementTree as ElementTree
from copy import copy
import json
import argparse
import sys

def get_path(xpath):
    return xpath.split("/")

def unique_paths(paths):
    path_dict = {}
    result = []
    for path in paths:
        path_dict["/".join(path)] = path
    for key in path_dict.keys():
        result.append(path_dict[key])
    return result

def kid_by_name(node, name):
    if "kids" in node.keys():
        for kid in node["kids"]:
            if "name" in kid.keys() and kid["name"] == name:
                return kid

def get_base(schema):
    root = schema["root"]
    conf = kid_by_name(root, "configuration")
    if conf == None:
        conf = root["kids"][0]["configuration"]
    return conf

def get_def(schema, path):
    kid = get_base(schema)
    for elem in path:
        kid = kid_by_name(kid, elem)
        if kid == None:
            break
    return kid

def get_json_config_subtree(schema, xpath):
    with open(schema) as f:
        schema = json.loads(f.read())
    config = { "root": { "name": "root", "kids": [ { "name": "configuration", "kids": [] } ] } }
    path = get_path(xpath)
    kids = config["root"]["kids"][0]["kids"]
    
    for elem in path[:-1]:
        kids.append({ "name": elem, "kids": [] })
        kids = kids[0]["kids"]
    d = get_def(schema, path)
    kids.append(d)

    return config

def main():
    # other arguments
    parser = argparse.ArgumentParser(exit_on_error=True)
    parser.add_argument('-j', '--json-schema', required=True, help='specify the json schema file')
    parser.add_argument('-x', '--xpath', required=True, help='specify the an xpath')
    args = parser.parse_args()

    config = get_json_config_subtree(args.json_schema, args.xpath)
    print(json.dumps(config, indent=2))
    
# run main()
if __name__ == "__main__":
    main()

