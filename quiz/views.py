import json
import math
from django.shortcuts import render, redirect, reverse
from django.contrib import messages
from django.urls import reverse_lazy
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic import (
    ListView,
    DetailView
)
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from .forms import UserRegisterForm, AnswerForm
from .models import Task, Card, Answer, Hint, SiteSetting, BombForfeit
from django.views.generic.edit import FormMixin


@login_required
def home(request):
    user = request.user
    if user.is_superuser or user.player_set.count() > 0:
        return render(request, 'quiz/home.html')
    else:
        messages.warning(request, f'Please enter the player details before attempting the challenge')
        return redirect('player-list')

def register(request):
    if request.method == 'POST':
        form = UserRegisterForm(request.POST)
        if form.is_valid():
            user = form.save(commit = False)
            user.save()
            messages.success(request, f'Account created successfully!')
            return redirect('login')
    else:
        form = UserRegisterForm()
    return render(request, 'quiz/register.html', {'form': form})

class TaskListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = Task
    template_name = 'quiz/task_list.html'
    context_object_name = 'tasks'

    def test_func(self):
        check = self.request.user.is_superuser or self.request.user.player_set.count() > 0
        if not check:
            messages.warning(self.request, f'Please enter the player details before attempting the challenge')
        return check

    def handle_no_permission(self):
        return redirect('player-list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        #if not self.request.user.is_superuser:
        card, _ = Card.objects.get_or_create(user=self.request.user)

        # Get current mode from site settings
        site_setting = SiteSetting.objects.first()
        current_mode = 'keyboardless' if (site_setting and site_setting.dark_mode) else 'mouseless'

        # Update user's phase if it doesn't match current mode
        if card.phase != current_mode:
            if current_mode == 'keyboardless':
                # When switching to keyboardless, save mouseless score
                card.update_mouseless_score()
                card.penalty_points = 0  # Reset penalties for new phase
            card.start = timezone.now()
            card.phase = current_mode
            card.save()

        context['card'] = card
        context['current_phase'] = current_mode

        return context

    def get_queryset(self):
        # Get current mode from site settings
        site_setting = SiteSetting.objects.first()
        current_mode = 'keyboardless' if (site_setting and site_setting.dark_mode) else 'mouseless'

        # Only show tasks for the current phase
        tasks = Task.objects.filter(question_type=current_mode).order_by('order')
        for task in tasks:
            task.completed = task.is_completed(self.request.user)
            task.forfeited = task.is_forfeited(self.request.user)
        return tasks



class TaskDetailView(LoginRequiredMixin, UserPassesTestMixin, FormMixin, DetailView):
    model = Task
    form_class = AnswerForm

    def get_success_url(self):
        return reverse('task-detail', kwargs={'pk': self.object.id})

    def test_func(self):
        check = self.request.user.is_superuser or self.request.user.player_set.count() > 0
        if not check:
            messages.warning(self.request, f'Please enter the player details before attempting the challenge')
        return check

    def handle_no_permission(self):
        return redirect('player-list')

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)


    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        task = self.object

        if not request.user.is_superuser and task.is_forfeited(request.user):
            messages.warning(request, f'"{task.name}" is no longer available — it was forfeited.')
            return redirect('task-list')

        card = self.request.user.card
        answer , _ = Answer.objects.get_or_create(card=card, task=task)

        # What's actually being submitted right now, if anything (empty on a plain page load)
        submitted_value = request.POST.get('value', '').strip()
        is_correct_submission = submitted_value != '' and submitted_value == task.correct

        self._bomb_is_first_open = False
        if not request.user.is_superuser and task.has_time_bomb and answer.value != task.correct and not is_correct_submission:
            now = timezone.now()
            if answer.bomb_started_at is None:
                # First time this user has opened this question - start the fuse now
                answer.bomb_started_at = now
                answer.save(update_fields=['bomb_started_at'])
                self._bomb_is_first_open = True
            elapsed = (now - answer.bomb_started_at).total_seconds()
            if elapsed >= task.bomb_duration:
                # Time ran out while they were away (e.g. on the hint page) - it still counts
                BombForfeit.objects.get_or_create(user=request.user, task=task)
                messages.warning(request, f'Time ran out on "{task.name}" while you were away — it has been forfeited.')
                return redirect('task-list')

        form = AnswerForm(request.POST, instance=answer)
        if form.is_valid():
            return self.form_valid(form)
        else:
            return self.form_invalid(form)


    def get_context_data(self, **kwargs):
        context = super(TaskDetailView, self).get_context_data(**kwargs)
        task = self.object
        card = self.request.user.card
        answer , _ = Answer.objects.get_or_create(card=card, task=task)
        context['form'] = AnswerForm(instance=answer)

        if not self.request.user.is_superuser and task.has_time_bomb and answer.value != task.correct:
            if answer.bomb_started_at:
                elapsed = (timezone.now() - answer.bomb_started_at).total_seconds()
                context['bomb_remaining'] = max(0, math.ceil(task.bomb_duration - elapsed))
            else:
                context['bomb_remaining'] = task.bomb_duration
            # Safe default of True: if this flag can't be determined for some reason,
            # we'd rather skip the leave-check than wrongly lock out a legitimate first view.
            context['bomb_is_first_open'] = getattr(self, '_bomb_is_first_open', True)

        return context

    def form_invalid(self, form):
        return super().form_invalid(form)

    def form_valid(self, form):
        form.instance.save()
        return super(TaskDetailView, self).form_valid(form)

@login_required
def leaderboard(request):
    leaderboard = list(filter(lambda t: t.score > 0 and not t.user.is_superuser, Card.objects.all()))
    if len(leaderboard) > 0:
        leaderboard = sorted(leaderboard, key=lambda t: (-t.score, t.last_time))[:10]

    # Get current mode for context
    site_setting = SiteSetting.objects.first()
    current_mode = 'keyboardless' if (site_setting and site_setting.dark_mode) else 'mouseless'

    context = {
        'leaderboard': leaderboard,
        'current_mode': current_mode
    }

    return render(request, 'quiz/leaderboard.html', context=context)

@login_required(login_url='login')
def showHint(request, pk):
    task = Task.objects.get(id=pk)
    card = Card.objects.get(user=request.user)
    hint_check = Hint.objects.filter(user=request.user, hint_task=task).exists()

    # Use current phase score for hint validation
    current_score = card.current_phase_score

    if ((current_score < task.hint_points) and (not(hint_check))):
        context = {
            'required_points': task.hint_points,
            'current_score': current_score,
            'task_name': task.name
        }
        return render(request, 'quiz/no_hint.html', context)
    else:
        if hint_check:
            hint = task.hint
            return render(request, 'quiz/hint.html', {'hint': hint, 'task_name': task.name})
        hint = task.hint
        card.penalty_points += task.hint_points
        card.save()
        h = Hint(user=request.user, hint_task=task)
        h.save()
        return render(request, 'quiz/hint.html', {'hint': hint, 'task_name': task.name})

@login_required(login_url='login')
@require_POST
def bombDetonate(request, pk):
    task = Task.objects.get(id=pk)

    if request.user.is_superuser or not task.has_time_bomb:
        return JsonResponse({'status': 'ignored'})

    # If they already solved it correctly (e.g. raced the timer), don't forfeit
    already_correct = Answer.objects.filter(card__user=request.user, task=task, value=task.correct).exists()
    if already_correct:
        return JsonResponse({'status': 'already_solved'})

    BombForfeit.objects.get_or_create(user=request.user, task=task)
    return JsonResponse({'status': 'forfeited'})

@login_required(login_url='login')
@require_POST
def cursorlessSolved(request, pk):
    task = Task.objects.get(id=pk)
    return JsonResponse({'answer': task.correct})

@login_required(login_url='login')
@require_POST
def bombVerifyOpen(request, pk):
    """
    Called by the client right after a bomb-question page loads (except on the
    very first legitimate open). Confirms whether this view is a safe
    continuation (a reload, or arriving from this task's own hint page) or
    whether the participant left the question and is trying to reopen it -
    in which case it is permanently forfeited, matching a normal detonation.
    """
    task = Task.objects.get(id=pk)

    if request.user.is_superuser or not task.has_time_bomb:
        return JsonResponse({'status': 'ok'})

    try:
        answer = Answer.objects.get(card__user=request.user, task=task)
    except Answer.DoesNotExist:
        return JsonResponse({'status': 'ok'})

    if answer.value == task.correct:
        return JsonResponse({'status': 'ok'})

    if BombForfeit.objects.filter(user=request.user, task=task).exists():
        return JsonResponse({'status': 'locked'})

    if answer.bomb_started_at is None:
        return JsonResponse({'status': 'ok'})

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        payload = {}

    is_reload = bool(payload.get('is_reload'))
    referrer = payload.get('referrer') or ''

    hint_path = reverse('show-hint', kwargs={'pk': pk})
    self_path = reverse('task-detail', kwargs={'pk': pk})

    safe = is_reload or (hint_path in referrer) or (self_path in referrer)

    if safe:
        return JsonResponse({'status': 'ok'})

    BombForfeit.objects.get_or_create(user=request.user, task=task)
    return JsonResponse({'status': 'locked'})

def get_dark_mode_setting():
    setting = SiteSetting.objects.first()
    return setting.dark_mode if setting else False

# Context processor to inject dark_mode_enabled into all templates
def dark_mode_context(request):
    return {'dark_mode_enabled': get_dark_mode_setting()}
