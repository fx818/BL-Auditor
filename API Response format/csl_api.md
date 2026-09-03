Dashboard
 >>Doclist
 >> Bizfeed
 >> GlAdminActivity/GetCSLData READ API
 >>View
GlAdminActivity/GetCSLData READ API
 1.0.0 
OAS3
The read service provides users activity data on indiamart platform like view product details page, viewed dashboard, Veiwed Company Detail Screen, Viewed Catalog Page etc. The API currently has a limit of 30 days for which user data is provided.
SRS Url - LINK

All the below parameters are case sensitive
(*) - Represents Mandatory parameters

The service supports only http request and works on both GET & POST methods

Servers

http://bizfeed-dev.imutils.com - Development Server
default

POST
​/GladminActivity​/GetCSLData​/
Parameters
Name	Description
glusrId *
string
(query)
GLID of the User

25739430
AK *
string
(query)
JWT authentication token for request validation.

eyJ0eXAiOiJKV1QiLCJhbGciOiJzaGEyNTYifQ.eyJpc3MiOiJDUk9OIiwiYXVkIjoiMy43LjIxNC45MyIsImV4cCI6MTgzODcwMzk0MiwiaWF0IjoxNjgxMDAzOTQyLCJzdWIiOiJBUElET0NTIn0.1M-38BjxIwU4BP1aHIdVwq-9Y4rl2du9jJjhwLkb2ec
flag *
string
(query)
Filter to provide paid and free seller data.

Values	Description
1	for url_weight = 0,1
2	for url_weight = 0
3	for url_weight = 1
Available values : 1, 2, 3


1
starttime *
string
(query)
start time value from which data is required

20230130
endtime *
string
(query)
end time value till which data is required

20230430
Responses
Code	Description	Links
200	
Request is Success.

The below is the response keys description

code - Represents the code based on whether request is success or failure.

datetime - Represents the input end datetime value.

Message - Provides the reason for the failure.

status - Represents the status of the request SUCCESS / FAILURE.

activity - stores the activity data invalid if request failed.

Data response keys	Description
adminln	cookie key for users login identifying if login via admin or user
catalog_owner_glusr_id	Glid of the visited catalog page of a seller
coordinate_accuracy	accuracy for the location details fetched during the activity by the browser
coordinate_latitude	users latitude value from which activity was performed
coordinate_longitude	users longitude value from which activity was performed
datevalue	date time on which activity was performed
domain_name	domain name value on which activity was performed
empid	employee id if indiamart employee is logged in
fk_activity_id	activity id signifying the activity
fk_display_title	display title for the activity performed
ga_utma_cookie	Google unique cookie fro user
gl_country	country from which user is logged in
gl_custtype_weight	customer type id for a user paid or free
glusr_id	users glid
glusr_usr_listing_status	users listing status with indiamart
http_status	status code for the activity performed
imeshvisitor_glusr_email	indiamart cookie for users email
imeshvisitor_glusr_id	indiamart cookie for users glid
insertion_time	insertion_time for the activity log of user
keyword	specific identifier keyword associated to few indiamart pages
location_pref_city_ids	users preferred city location id values
location_pref_city_names	users preferred city location names values
log_status_flag	status of generation of activity log
mcat_ids	users visited mcat ids
mcat_names	users visited mcat names
modid	Module id of the platform from which activity is done
owner_gl_country	user visited seller country name
owner_glusr_usr_listing_status	user visited seller listing status
product_disp_id	product display id if user visited a product
referer	referer from which users log was captured
remote_ip	remote_ip of the user while doing activity
request_url	request url of the page user visited
response_size	size of the log packet generated
response_time	time to capture the activity log
server_name	server_name fro which log was generated
url_weight	url weight
user_agent	user agent for the users login
v4iilex_glusr_email	indiamart email cookie
v4iilex_glusr_id	indiamart glid cookie
catid	catagory id for porduct visited
grpid	group id for product veiwed
mcatid	mcat id for product
sllrcityid	city id of the seller
glb_city	city id mapped globally for the user activity
glb_state	state id mapped globally for the user activity
glb_longitude	longitude coordinate captured globally for the user activity
glb_latitude	latitude coordinate captured globally for the user activity
Media type

application/json
Controls Accept header.
Examples

Data found
Example Value
Schema
{
  "activity": {
    "20240903065905": [
      {
        "adminln": "-",
        "catalog_owner_glusr_id": 0,
        "coordinate_accuracy": 12.6,
        "coordinate_latitude": 28.733887,
        "coordinate_longitude": 77.17862,
        "datevalue": "20240903065905",
        "domain_name": "-",
        "empid": 0,
        "fk_activity_id": 2175,
        "fk_display_title": "Buyer Dashboard Screen",
        "ga_utma_cookie": "-",
        "gl_country": "IN",
        "gl_custtype_weight": 1299,
        "glusr_id": 218388034,
        "glusr_usr_listing_status": "LST",
        "http_status": 0,
        "imeshvisitor_glusr_email": "-",
        "imeshvisitor_glusr_id": 0,
        "insertion_time": {
          "type": {
            "name": "bigint"
          },
          "value": "202409030659"
        },
        "keyword": null,
        "location_pref_city_ids": {
          "type": {
            "valueType": {
              "name": "int"
            }
          },
          "values": [
            70422
          ]
        },
        "location_pref_city_names": null,
        "log_status_flag": 40,
        "mcat_ids": {
          "type": {
            "valueType": {
              "name": "int"
            }
          },
          "values": [
            0
          ]
        },
        "mcat_names": null,
        "modid": "ANDROID",
        "owner_gl_country": "-",
        "owner_gl_custtype_weight": 0,
        "owner_glusr_usr_listing_status": "-",
        "product_disp_id": null,
        "referer": "-",
        "remote_ip": "42.105.77.157",
        "request_url": "BuyerDashboard",
        "response_size": 0,
        "response_time": 0,
        "server_name": 0,
        "url_weight": 1,
        "user_agent": null,
        "v4iilex_glusr_email": "-",
        "v4iilex_glusr_id": 0,
        "catid": "321",
        "grpid": "321",
        "mcatid": "21312",
        "sllrcityid": "1231",
        "glb_city": "0",
        "glb_state": "0",
        "glb_longitude": "0",
        "glb_latitude": "0"
      },
      {
        "adminln": "-",
        "catalog_owner_glusr_id": 0,
        "coordinate_accuracy": 12.6,
        "coordinate_latitude": 28.733887,
        "coordinate_longitude": 77.17862,
        "datevalue": "20240903065905",
        "domain_name": "-",
        "empid": 0,
        "fk_activity_id": 2175,
        "fk_display_title": "Buyer Dashboard Screen",
        "ga_utma_cookie": "-",
        "gl_country": "IN",
        "gl_custtype_weight": 1299,
        "glusr_id": 218388034,
        "glusr_usr_listing_status": "LST",
        "http_status": 0,
        "imeshvisitor_glusr_email": "-",
        "imeshvisitor_glusr_id": 0,
        "insertion_time": {
          "type": {
            "name": "bigint"
          },
          "value": "202409030659"
        },
        "keyword": null,
        "location_pref_city_ids": {
          "type": {
            "valueType": {
              "name": "int"
            }
          },
          "values": [
            0
          ]
        },
        "location_pref_city_names": null,
        "log_status_flag": 40,
        "mcat_ids": {
          "type": {
            "valueType": {
              "name": "int"
            }
          },
          "values": [
            0
          ]
        },
        "mcat_names": null,
        "modid": "ANDROID",
        "owner_gl_country": "-",
        "owner_gl_custtype_weight": 0,
        "owner_glusr_usr_listing_status": "-",
        "product_disp_id": null,
        "referer": "-",
        "remote_ip": "42.105.77.157",
        "request_url": "BuyerDashboard",
        "response_size": 0,
        "response_time": 0,
        "server_name": 0,
        "url_weight": 1,
        "user_agent": null,
        "v4iilex_glusr_email": "-",
        "v4iilex_glusr_id": 0,
        "catid": "321",
        "grpid": "321",
        "mcatid": "21312",
        "sllrcityid": "1231",
        "glb_city": "0",
        "glb_state": "0",
        "glb_longitude": "0",
        "glb_latitude": "0"
      }
    ],
    "20240903142132": [
      {
        "adminln": "-",
        "catalog_owner_glusr_id": 0,
        "coordinate_accuracy": 39.6,
        "coordinate_latitude": 28.072409,
        "coordinate_longitude": 76.53144,
        "datevalue": "20240903142132",
        "domain_name": "-",
        "empid": 0,
        "fk_activity_id": 2192,
        "fk_display_title": "MBR Screen",
        "ga_utma_cookie": "-",
        "gl_country": "IN",
        "gl_custtype_weight": 1299,
        "glusr_id": 218388034,
        "glusr_usr_listing_status": "LST",
        "http_status": 0,
        "imeshvisitor_glusr_email": "-",
        "imeshvisitor_glusr_id": 0,
        "insertion_time": {
          "type": {
            "name": "bigint"
          },
          "value": "202409031421"
        },
        "keyword": null,
        "location_pref_city_ids": {
          "type": {
            "valueType": {
              "name": "int"
            }
          },
          "values": [
            0
          ]
        },
        "location_pref_city_names": null,
        "log_status_flag": 40,
        "mcat_ids": {
          "type": {
            "valueType": {
              "name": "int"
            }
          },
          "values": [
            0
          ]
        },
        "mcat_names": null,
        "modid": "ANDROID",
        "owner_gl_country": "-",
        "owner_gl_custtype_weight": 0,
        "owner_glusr_usr_listing_status": "-",
        "product_disp_id": null,
        "referer": "-",
        "remote_ip": "152.58.75.18",
        "request_url": "Manage Buy Requirement",
        "response_size": 0,
        "response_time": 0,
        "server_name": 0,
        "url_weight": 1,
        "user_agent": null,
        "v4iilex_glusr_email": "-",
        "v4iilex_glusr_id": 0,
        "catid": "321",
        "grpid": "321",
        "mcatid": "21312",
        "sllrcityid": "1231",
        "glb_city": "0",
        "glb_state": "0",
        "glb_longitude": "0",
        "glb_latitude": "0"
      }
    ]
  },
  "code": 200,
  "datetime": "20240903235959",
  "message": "Data found",
  "status": "SUCCESS"
}
No links
400	
Request is Failed.

The below is the response keys description

code - Represents the code based on whether request is success or failure.

datetime - Represents the input end datetime value.

Message - Provides the reason for the failure.

status - Represents the status of the request SUCCESS / FAILURE.

activity - stores the activity data invalid if request failed.

Media type

application/json
Examples

Mandatory Parameters are missing (glusrId/starttime/endtime/flag).
Example Value
Schema
{
  "activity": "invalid",
  "code": 400,
  "datetime": "00010101000000",
  "message": "Some mandatory parameter missing - glusrId/starttime/endtime/flag",
  "status": "FAILURE"
}
No links
404	
Request is Failed.

The below is the response keys description

CODE - Represents the code based on whether request is success or failure.

MESSAGE - Provides the reason for the failure.

Media type

application/json
Examples

ak invalid
Example Value
Schema
{
  "CODE": "404",
  "MESSAGE": "JWT VALIDATED BUT INPUT IP DIDNOT MATCH WITH PAYLOAD IP IN CRON TOKEN",
  "unique_id": "NQ29A1755080298"
}
No links
GET
​/GladminActivity​/GetCSLData​/
Parameters
Name	Description
glusrId *
string
(query)
GLID of the User

25739430
AK *
string
(query)
JWT authentication token for request validation.

eyJ0eXAiOiJKV1QiLCJhbGciOiJzaGEyNTYifQ.eyJpc3MiOiJDUk9OIiwiYXVkIjoiMy43LjIxNC45MyIsImV4cCI6MTgzODcwMzk0MiwiaWF0IjoxNjgxMDAzOTQyLCJzdWIiOiJBUElET0NTIn0.1M-38BjxIwU4BP1aHIdVwq-9Y4rl2du9jJjhwLkb2ec
flag *
string
(query)
Filter to provide paid and free seller data.

Values	Description
1	for url_weight = 0,1
2	for url_weight = 0
3	for url_weight = 1
Available values : 1, 2, 3


1
starttime *
string
(query)
start time value from which data is required

20230130
endtime *
string
(query)
end time value till which data is required

20230430
Responses
Code	Description	Links
200	
Request is Success.

The below is the response keys description

code - Represents the code based on whether request is success or failure.

datetime - Represents the input end datetime value.

Message - Provides the reason for the failure.

status - Represents the status of the request SUCCESS / FAILURE.

activity - stores the activity data invalid if request failed.

Data response keys	Description
adminln	cookie key for users login identifying if login via admin or user
catalog_owner_glusr_id	Glid of the visited catalog page of a seller
coordinate_accuracy	accuracy for the location details fetched during the activity by the browser
coordinate_latitude	users latitude value from which activity was performed
coordinate_longitude	users longitude value from which activity was performed
datevalue	date time on which activity was performed
domain_name	domain name value on which activity was performed
empid	employee id if indiamart employee is logged in
fk_activity_id	activity id signifying the activity
fk_display_title	display title for the activity performed
ga_utma_cookie	Google unique cookie fro user
gl_country	country from which user is logged in
gl_custtype_weight	customer type id for a user paid or free
glusr_id	users glid
glusr_usr_listing_status	users listing status with indiamart
http_status	status code for the activity performed
imeshvisitor_glusr_email	indiamart cookie for users email
imeshvisitor_glusr_id	indiamart cookie for users glid
insertion_time	insertion_time for the activity log of user
keyword	specific identifier keyword associated to few indiamart pages
location_pref_city_ids	users preferred city location id values
location_pref_city_names	users preferred city location names values
log_status_flag	status of generation of activity log
mcat_ids	users visited mcat ids
mcat_names	users visited mcat names
modid	Module id of the platform from which activity is done
owner_gl_country	user visited seller country name
owner_glusr_usr_listing_status	user visited seller listing status
product_disp_id	product display id if user visited a product
referer	referer from which users log was captured
remote_ip	remote_ip of the user while doing activity
request_url	request url of the page user visited
response_size	size of the log packet generated
response_time	time to capture the activity log
server_name	server_name fro which log was generated
url_weight	url weight
user_agent	user agent for the users login
v4iilex_glusr_email	indiamart email cookie
v4iilex_glusr_id	indiamart glid cookie
catid	catagory id for porduct visited
grpid	group id for product veiwed
mcatid	mcat id for product
sllrcityid	city id of the seller
glb_city	city id mapped globally for the user activity
glb_state	state id mapped globally for the user activity
glb_longitude	longitude coordinate captured globally for the user activity
glb_latitude	latitude coordinate captured globally for the user activity
Media type

application/json
Controls Accept header.
Examples

Data found
Example Value
Schema
{
  "activity": {
    "20240903065905": [
      {
        "adminln": "-",
        "catalog_owner_glusr_id": 0,
        "coordinate_accuracy": 12.6,
        "coordinate_latitude": 28.733887,
        "coordinate_longitude": 77.17862,
        "datevalue": "20240903065905",
        "domain_name": "-",
        "empid": 0,
        "fk_activity_id": 2175,
        "fk_display_title": "Buyer Dashboard Screen",
        "ga_utma_cookie": "-",
        "gl_country": "IN",
        "gl_custtype_weight": 1299,
        "glusr_id": 218388034,
        "glusr_usr_listing_status": "LST",
        "http_status": 0,
        "imeshvisitor_glusr_email": "-",
        "imeshvisitor_glusr_id": 0,
        "insertion_time": {
          "type": {
            "name": "bigint"
          },
          "value": "202409030659"
        },
        "keyword": null,
        "location_pref_city_ids": {
          "type": {
            "valueType": {
              "name": "int"
            }
          },
          "values": [
            70422
          ]
        },
        "location_pref_city_names": null,
        "log_status_flag": 40,
        "mcat_ids": {
          "type": {
            "valueType": {
              "name": "int"
            }
          },
          "values": [
            0
          ]
        },
        "mcat_names": null,
        "modid": "ANDROID",
        "owner_gl_country": "-",
        "owner_gl_custtype_weight": 0,
        "owner_glusr_usr_listing_status": "-",
        "product_disp_id": null,
        "referer": "-",
        "remote_ip": "42.105.77.157",
        "request_url": "BuyerDashboard",
        "response_size": 0,
        "response_time": 0,
        "server_name": 0,
        "url_weight": 1,
        "user_agent": null,
        "v4iilex_glusr_email": "-",
        "v4iilex_glusr_id": 0,
        "catid": "321",
        "grpid": "321",
        "mcatid": "21312",
        "sllrcityid": "1231",
        "glb_city": "0",
        "glb_state": "0",
        "glb_longitude": "0",
        "glb_latitude": "0"
      },
      {
        "adminln": "-",
        "catalog_owner_glusr_id": 0,
        "coordinate_accuracy": 12.6,
        "coordinate_latitude": 28.733887,
        "coordinate_longitude": 77.17862,
        "datevalue": "20240903065905",
        "domain_name": "-",
        "empid": 0,
        "fk_activity_id": 2175,
        "fk_display_title": "Buyer Dashboard Screen",
        "ga_utma_cookie": "-",
        "gl_country": "IN",
        "gl_custtype_weight": 1299,
        "glusr_id": 218388034,
        "glusr_usr_listing_status": "LST",
        "http_status": 0,
        "imeshvisitor_glusr_email": "-",
        "imeshvisitor_glusr_id": 0,
        "insertion_time": {
          "type": {
            "name": "bigint"
          },
          "value": "202409030659"
        },
        "keyword": null,
        "location_pref_city_ids": {
          "type": {
            "valueType": {
              "name": "int"
            }
          },
          "values": [
            0
          ]
        },
        "location_pref_city_names": null,
        "log_status_flag": 40,
        "mcat_ids": {
          "type": {
            "valueType": {
              "name": "int"
            }
          },
          "values": [
            0
          ]
        },
        "mcat_names": null,
        "modid": "ANDROID",
        "owner_gl_country": "-",
        "owner_gl_custtype_weight": 0,
        "owner_glusr_usr_listing_status": "-",
        "product_disp_id": null,
        "referer": "-",
        "remote_ip": "42.105.77.157",
        "request_url": "BuyerDashboard",
        "response_size": 0,
        "response_time": 0,
        "server_name": 0,
        "url_weight": 1,
        "user_agent": null,
        "v4iilex_glusr_email": "-",
        "v4iilex_glusr_id": 0,
        "catid": "321",
        "grpid": "321",
        "mcatid": "21312",
        "sllrcityid": "1231",
        "glb_city": "0",
        "glb_state": "0",
        "glb_longitude": "0",
        "glb_latitude": "0"
      }
    ],
    "20240903142132": [
      {
        "adminln": "-",
        "catalog_owner_glusr_id": 0,
        "coordinate_accuracy": 39.6,
        "coordinate_latitude": 28.072409,
        "coordinate_longitude": 76.53144,
        "datevalue": "20240903142132",
        "domain_name": "-",
        "empid": 0,
        "fk_activity_id": 2192,
        "fk_display_title": "MBR Screen",
        "ga_utma_cookie": "-",
        "gl_country": "IN",
        "gl_custtype_weight": 1299,
        "glusr_id": 218388034,
        "glusr_usr_listing_status": "LST",
        "http_status": 0,
        "imeshvisitor_glusr_email": "-",
        "imeshvisitor_glusr_id": 0,
        "insertion_time": {
          "type": {
            "name": "bigint"
          },
          "value": "202409031421"
        },
        "keyword": null,
        "location_pref_city_ids": {
          "type": {
            "valueType": {
              "name": "int"
            }
          },
          "values": [
            0
          ]
        },
        "location_pref_city_names": null,
        "log_status_flag": 40,
        "mcat_ids": {
          "type": {
            "valueType": {
              "name": "int"
            }
          },
          "values": [
            0
          ]
        },
        "mcat_names": null,
        "modid": "ANDROID",
        "owner_gl_country": "-",
        "owner_gl_custtype_weight": 0,
        "owner_glusr_usr_listing_status": "-",
        "product_disp_id": null,
        "referer": "-",
        "remote_ip": "152.58.75.18",
        "request_url": "Manage Buy Requirement",
        "response_size": 0,
        "response_time": 0,
        "server_name": 0,
        "url_weight": 1,
        "user_agent": null,
        "v4iilex_glusr_email": "-",
        "v4iilex_glusr_id": 0,
        "catid": "321",
        "grpid": "321",
        "mcatid": "21312",
        "sllrcityid": "1231",
        "glb_city": "0",
        "glb_state": "0",
        "glb_longitude": "0",
        "glb_latitude": "0"
      }
    ]
  },
  "code": 200,
  "datetime": "20240903235959",
  "message": "Data found",
  "status": "SUCCESS"
}
No links
400	
Request is Failed.

The below is the response keys description

code - Represents the code based on whether request is success or failure.

datetime - Represents the input end datetime value.

Message - Provides the reason for the failure.

status - Represents the status of the request SUCCESS / FAILURE.

activity - stores the activity data invalid if request failed.

Media type

application/json
Examples

Mandatory Parameters are missing (glusrId/starttime/endtime/flag).
Example Value
Schema
{
  "activity": "invalid",
  "code": 400,
  "datetime": "00010101000000",
  "message": "Some mandatory parameter missing - glusrId/starttime/endtime/flag",
  "status": "FAILURE"
}
No links
404	
Request is Failed.

The below is the response keys description

CODE - Represents the code based on whether request is success or failure.

MESSAGE - Provides the reason for the failure.

Media type

application/json
Examples

ak invalid
Example Value
Schema
{
  "CODE": "404",
  "MESSAGE": "JWT VALIDATED BUT INPUT IP DIDNOT MATCH WITH PAYLOAD IP IN CRON TOKEN",
  "unique_id": "NQ29A1755080298"
}
No links
