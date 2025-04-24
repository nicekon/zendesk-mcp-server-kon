from typing import Dict, Any, List

from zenpy import Zenpy
from zenpy.lib.api_objects import Comment
import requests
from requests.auth import HTTPBasicAuth


class ZendeskClient:
    def __init__(self, subdomain: str, email: str, token: str):
        """
        Initialize the Zendesk client using zenpy lib.
        """
        self.client = Zenpy(
            subdomain=subdomain,
            email=email,
            token=token
        )
        self.subdomain = subdomain
        self.email = email
        self.token = token

    def get_ticket(self, ticket_id: int) -> Dict[str, Any]:
        """
        Query a ticket by its ID
        """
        try:
            ticket = self.client.tickets(id=ticket_id)
            return {
                'id': ticket.id,
                'subject': ticket.subject,
                'description': ticket.description,
                'status': ticket.status,
                'priority': ticket.priority,
                'created_at': str(ticket.created_at),
                'updated_at': str(ticket.updated_at),
                'requester_id': ticket.requester_id,
                'assignee_id': ticket.assignee_id,
                'organization_id': ticket.organization_id
            }
        except Exception as e:
            raise Exception(f"Failed to get ticket {ticket_id}: {str(e)}")

    def get_ticket_comments(self, ticket_id: int) -> List[Dict[str, Any]]:
        """
        Get all comments for a specific ticket.
        """
        try:
            comments = self.client.tickets.comments(ticket=ticket_id)
            return [{
                'id': comment.id,
                'author_id': comment.author_id,
                'body': comment.body,
                'html_body': comment.html_body,
                'public': comment.public,
                'created_at': str(comment.created_at)
            } for comment in comments]
        except Exception as e:
            raise Exception(f"Failed to get comments for ticket {ticket_id}: {str(e)}")

    def post_comment(self, ticket_id: int, comment: str, public: bool = True) -> str:
        """
        Post a comment to an existing ticket.
        """
        try:
            ticket = self.client.tickets(id=ticket_id)
            ticket.comment = Comment(
                html_body=comment,
                public=public
            )
            self.client.tickets.update(ticket)
            return comment
        except Exception as e:
            raise Exception(f"Failed to post comment on ticket {ticket_id}: {str(e)}")

    def get_all_articles(self) -> Dict[str, Any]:
        """
        Fetch help center articles as knowledge base.
        Returns a Dict of section -> [article].
        """
        try:
            # Get all sections
            sections = self.client.help_center.sections()

            # Get articles for each section
            kb = {}
            for section in sections:
                articles = self.client.help_center.sections.articles(section.id)
                kb[section.name] = {
                    'section_id': section.id,
                    'description': section.description,
                    'articles': [{
                        'id': article.id,
                        'title': article.title,
                        'body': article.body,
                        'updated_at': str(article.updated_at),
                        'url': article.html_url
                    } for article in articles]
                }

            return kb
        except Exception as e:
            raise Exception(f"Failed to fetch knowledge base: {str(e)}")

    def get_community_posts(self, filter_by: str = None, sort_by: str = None) -> List[Dict[str, Any]]:
        """
        Get community posts with optional filtering and sorting.
        
        Args:
            filter_by (str, optional): Filter posts by status. Possible values: "planned", "not_planned", "completed", "answered", "none"
            sort_by (str, optional): Sort posts by criteria. Possible values: "created_at", "edited_at", "updated_at", "recent_activity", "votes", "comments"
        
        Returns:
            List[Dict[str, Any]]: List of community posts
        """
        try:
            url = f"https://{self.subdomain}.zendesk.com/api/v2/community/posts.json"
            params = {}
            if filter_by:
                params["filter_by"] = filter_by
            if sort_by:
                params["sort_by"] = sort_by

            auth = HTTPBasicAuth(f"{self.email}/token", self.token)
            response = requests.get(url, params=params, auth=auth)
            response.raise_for_status()
            
            data = response.json()
            posts = data.get("posts", [])
            
            # 상담원 작성자 표시 추가
            for post in posts:
                if post.get("author_id") == 363616557374:
                    post["author_role"] = "상담원"
            
            return posts
        except Exception as e:
            raise Exception(f"Failed to get community posts: {str(e)}")

    def get_community_post_comments(self, post_id: int) -> Dict[str, Any]:
        """
        Get all comments for a specific community post and the post itself.
        
        Args:
            post_id (int): The ID of the post to get comments for
        
        Returns:
            Dict[str, Any]: Dictionary containing the post and its comments
        """
        try:
            # Get post details
            post_url = f"https://{self.subdomain}.zendesk.com/api/v2/community/posts/{post_id}.json"
            auth = HTTPBasicAuth(f"{self.email}/token", self.token)
            post_response = requests.get(post_url, auth=auth)
            post_response.raise_for_status()
            post_data = post_response.json()
            
            # Get comments
            comments_url = f"https://{self.subdomain}.zendesk.com/api/v2/community/posts/{post_id}/comments.json"
            comments_response = requests.get(comments_url, auth=auth)
            comments_response.raise_for_status()
            comments_data = comments_response.json()
            
            # 상담원 작성자 표시 추가 (게시물)
            post = post_data.get("post", {})
            if post.get("author_id") == 363616557374:
                post["author_role"] = "상담원"
            
            # 상담원 작성자 표시 추가 (댓글)
            comments = comments_data.get("comments", [])
            for comment in comments:
                if comment.get("author_id") == 363616557374:
                    comment["author_role"] = "상담원"
            
            return {
                "post": post,
                "comments": comments
            }
        except Exception as e:
            raise Exception(f"Failed to get post and comments for post {post_id}: {str(e)}")

    def get_community_topics(self) -> List[Dict[str, Any]]:
        """
        Get all community topics.
        
        Returns:
            List[Dict[str, Any]]: List of community topics
        """
        try:
            url = f"https://{self.subdomain}.zendesk.com/api/v2/community/topics.json"
            auth = HTTPBasicAuth(f"{self.email}/token", self.token)
            response = requests.get(url, auth=auth)
            response.raise_for_status()
            
            data = response.json()
            topics = data.get("topics", [])
            
            return topics
        except Exception as e:
            raise Exception(f"Failed to get community topics: {str(e)}")

    def create_community_post_comment(self, post_id: int, body: str, author_id: int = None, notify_subscribers: bool = True) -> Dict[str, Any]:
        """
        커뮤니티 포스트에 댓글을 작성합니다.
        
        Args:
            post_id (int): 댓글을 작성할 포스트의 ID
            body (str): 댓글 내용(p, br, strong 태그 사용)
            author_id (int, optional): 댓글 작성자 ID (Help Center 관리자만 사용 가능)
            notify_subscribers (bool, optional): 구독자에게 알림을 보낼지 여부
        
        Returns:
            Dict[str, Any]: 생성된 댓글 정보
        """
        try:
            url = f"https://{self.subdomain}.zendesk.com/api/v2/community/posts/{post_id}/comments.json"
            auth = HTTPBasicAuth(f"{self.email}/token", self.token)
            
            data = {
                "comment": {
                    "body": body
                }
            }
            
            if author_id is not None:
                data["comment"]["author_id"] = author_id
            
            if not notify_subscribers:
                data["notify_subscribers"] = False
            
            response = requests.post(url, json=data, auth=auth)
            response.raise_for_status()
            
            return response.json()["comment"]
        except Exception as e:
            raise Exception(f"Failed to create comment on post {post_id}: {str(e)}")

    def update_community_post_comment(self, post_id: int, comment_id: int, body: str) -> Dict[str, Any]:
        """
        커뮤니티 포스트의 댓글을 수정합니다.
        
        Args:
            post_id (int): 댓글이 속한 포스트의 ID
            comment_id (int): 수정할 댓글의 ID
            body (str): 댓글 내용(p, br, strong 태그 사용)
        
        Returns:
            Dict[str, Any]: 수정된 댓글 정보
        """
        try:
            url = f"https://{self.subdomain}.zendesk.com/api/v2/community/posts/{post_id}/comments/{comment_id}.json"
            auth = HTTPBasicAuth(f"{self.email}/token", self.token)
            
            data = {
                "comment": {
                    "body": body
                }
            }
            
            response = requests.put(url, json=data, auth=auth)
            response.raise_for_status()
            
            return response.json()["comment"]
        except Exception as e:
            raise Exception(f"Failed to update comment {comment_id} on post {post_id}: {str(e)}")

    def update_community_post(self, post_id: int, title: str = None, details: str = None, topic_id: int = None, status: str = None) -> Dict[str, Any]:
        """
        커뮤니티 포스트를 수정합니다.
        
        Args:
            post_id (int): 수정할 포스트의 ID
            title (str, optional): 포스트 제목
            details (str, optional): 포스트 내용(p, br, strong 태그 사용 가능)
            topic_id (int, optional): 포스트가 속할 토픽의 ID
            status (str, optional): 포스트 상태 ("planned", "not_planned", "answered", "completed" 중 하나)
        
        Returns:
            Dict[str, Any]: 수정된 포스트 정보
        """
        try:
            url = f"https://{self.subdomain}.zendesk.com/api/v2/community/posts/{post_id}.json"
            auth = HTTPBasicAuth(f"{self.email}/token", self.token)
            
            data = {
                "post": {}
            }
            
            if title is not None:
                data["post"]["title"] = title
            if details is not None:
                data["post"]["details"] = details
            if topic_id is not None:
                data["post"]["topic_id"] = topic_id
            if status is not None:
                data["post"]["status"] = status
            
            response = requests.put(url, json=data, auth=auth)
            response.raise_for_status()
            
            return response.json()["post"]
        except Exception as e:
            raise Exception(f"Failed to update post {post_id}: {str(e)}")
