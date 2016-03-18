# Solr howto

## Start a docker container

```
docker run --name pioapi-solr -d -t -p 127.0.0.1:8983:8983 solr:5.5.0 
```

Where `127.0.0.1:8983` is an IP and a port of the host to bind to. If IP
is omitted, the container will bind to `0.0.0.0` (to all interfaces). 

The `8983` on the right is a port exposed by a container. It should 
remain the same. The left one may be set to any available port. You 
can run multiple containers on one host with different ports set. For 
example, one with `-p 9001:8983` and another with `-p 9002:8983`.


## Create [a core](https://cwiki.apache.org/confluence/display/solr/Solr+Cores+and+solr.xml)

```
docker exec -it --user=solr pioapi-solr bin/solr create_core -c libs
```

Where `libs` is a name of the core. It should be specified in API config
(along with the host and port of Solr instance).


## Modify the API configuration

Add the `SOLR_LIBS_URI` to the server config in the following format:
`http://{host}:{port}/solr/{core}`.


## Add the existing data into the Solr index

```
platformio-api initialize_solr_for_libs
```
